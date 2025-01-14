import socket
import struct
import threading
import time

# =============================================================================
#                               CONSTANTS
# =============================================================================

OFFER_MAGIC_COOKIE = 0xabcddcba
OFFER_MESSAGE_TYPE = 0x2
REQUEST_MESSAGE_TYPE = 0x3
PAYLOAD_MESSAGE_TYPE = 0x4

UDP_BROADCAST_PORT = 13117   # Where the client listens for broadcast offers

# For color
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"

# Event to let us stop the client gracefully if needed
client_shutdown_event = threading.Event()

# =============================================================================
#                               CLIENT CODE
# =============================================================================

def udp_discover():
    """
    Listens on UDP_BROADCAST_PORT (13117) for an offer.
    Once an offer is received, returns (server_ip, server_udp_port, server_tcp_port).
    If no offer arrives, returns (None, None, None).
    """
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Allow re-use so multiple clients can bind the same port
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_socket.bind(("", UDP_BROADCAST_PORT))

    print(f"{CYAN}Listening for server offers on UDP port {UDP_BROADCAST_PORT}...{RESET}")

    while not client_shutdown_event.is_set():
        udp_socket.settimeout(3)  # Wait up to 3 seconds for broadcast
        try:
            data, addr = udp_socket.recvfrom(1024)
        except socket.timeout:
            print(f"{YELLOW}No offers received yet... retrying.{RESET}")
            continue
        except KeyboardInterrupt:
            print(f"{RED}Client interrupted, shutting down...{RESET}")
            break

        try:
            magic_cookie, msg_type, udp_port, tcp_port = struct.unpack('!IBHH', data)
            if magic_cookie == OFFER_MAGIC_COOKIE and msg_type == OFFER_MESSAGE_TYPE:
                print(f"{GREEN}Received offer from {addr[0]}:{RESET} UDP={udp_port}, TCP={tcp_port}")
                udp_socket.close()
                return addr[0], udp_port, tcp_port
        except Exception as e:
            print(f"{RED}Error parsing offer: {e}{RESET}")

    udp_socket.close()
    return None, None, None


def perform_speed_test(server_ip, server_udp_port, server_tcp_port, file_size, tcp_connections, udp_connections):
    """
    Spawns threads to do multiple TCP and UDP transfers in parallel.
    Prints partial progress, calculates average speed, and (for UDP) measures simple jitter.
    """

    # We'll store partial progress for each connection
    # Key: thread_id, Value: number of bytes or packets received so far
    progress_dict = {}
    progress_lock = threading.Lock()

    def print_progress(identifier, current, total):
        """
        Simple text progress bar or partial progress, for demonstration.
        """
        bar_length = 20
        fraction = current / total if total > 0 else 1
        filled = int(bar_length * fraction)
        bar = '#' * filled + '-' * (bar_length - filled)
        print(f"{CYAN}[{identifier}] [{bar}] {current}/{total}{RESET}", end='\r')

    def tcp_test(thread_id):
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        start_time = time.time()

        # We'll read data in chunks to show partial progress
        chunk_size = 1024
        bytes_received = 0
        data_buf = b""

        try:
            tcp_socket.settimeout(5)  # fail if 5s no response
            tcp_socket.connect((server_ip, server_tcp_port))
            tcp_socket.sendall(f"{file_size}\n".encode())

            # Keep receiving until we have 'file_size' bytes
            while bytes_received < file_size:
                chunk = tcp_socket.recv(chunk_size)
                if not chunk:
                    break
                data_buf += chunk
                bytes_received += len(chunk)

                # Update progress
                with progress_lock:
                    progress_dict[thread_id] = bytes_received
                    print_progress(f"TCP-{thread_id}", bytes_received, file_size)

            total_time = time.time() - start_time
            speed = bytes_received / total_time if total_time > 0 else 0
            print(f"\n{GREEN}[TCP-{thread_id}] Received {bytes_received} bytes in {total_time:.2f}s, speed={speed:.2f} B/s{RESET}")

        except Exception as e:
            print(f"\n{RED}[TCP-{thread_id}] Error: {e}{RESET}")
        finally:
            tcp_socket.close()

    def udp_test(thread_id):
        """
        For UDP, we measure how many packets arrive, plus a naive "jitter" calculation:
        Jitter = average of absolute differences between consecutive packet arrival times.
        """
        udp_socket_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket_local.settimeout(5)

        request_msg = struct.pack('!IBQ', OFFER_MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, file_size)
        start_time = time.time()

        arrival_times = []
        received_packets = 0
        try:
            # Send request to server
            udp_socket_local.sendto(request_msg, (server_ip, server_udp_port))

            while True:
                try:
                    data, _ = udp_socket_local.recvfrom(2048)
                except socket.timeout:
                    # If no data for 5s, we assume we're done
                    break
                arrival_times.append(time.time())
                received_packets += 1

                # Update partial progress (just the packet count)
                with progress_lock:
                    progress_dict[thread_id] = received_packets
                    expected_packets = file_size // 1024
                    print_progress(f"UDP-{thread_id}", received_packets, expected_packets)

            total_time = time.time() - start_time
            expected_packets = file_size // 1024
            lost_packets = expected_packets - received_packets

            # Calculate simple jitter
            # Jitter = average of absolute differences between consecutive arrival times
            if len(arrival_times) > 1:
                deltas = []
                for i in range(1, len(arrival_times)):
                    deltas.append(abs(arrival_times[i] - arrival_times[i - 1]))
                jitter = sum(deltas) / len(deltas)
            else:
                jitter = 0.0

            # Speed in bytes/s
            # We assume each packet is 1024 bytes of payload
            speed = (received_packets * 1024) / total_time if total_time > 0 else 0

            print(f"\n{GREEN}[UDP-{thread_id}] Received {received_packets}/{expected_packets} packets in {total_time:.2f}s "
                  f"(loss={lost_packets}, jitter={jitter:.4f}s), speed={speed:.2f} B/s{RESET}")

        except Exception as e:
            print(f"\n{RED}[UDP-{thread_id}] Error: {e}{RESET}")
        finally:
            udp_socket_local.close()

    # Start threads
    threads = []
    # We'll label them 1..n for TCP, 1..n for UDP
    for i in range(tcp_connections):
        thread_id = f"T{i+1}"
        progress_dict[thread_id] = 0
        t = threading.Thread(target=tcp_test, args=(thread_id,))
        t.start()
        threads.append(t)

    for i in range(udp_connections):
        thread_id = f"U{i+1}"
        progress_dict[thread_id] = 0
        t = threading.Thread(target=udp_test, args=(thread_id,))
        t.start()
        threads.append(t)

    # Wait for all to finish
    for t in threads:
        t.join()

    print(f"{CYAN}All transfers completed!\n{RESET}")


def main():
    """
    - Ask user for desired file size, number of TCP/UDP connections.
    - Discover server via broadcast.
    - Perform the speed test.
    """
    print(f"{GREEN}Welcome to the Speed Test Client!{RESET}")

    # 1) Get user input
    while True:
        try:
            file_size = int(input(f"{YELLOW}Enter file size in bytes (e.g. 10240): {RESET}"))
            if file_size < 1:
                raise ValueError
            break
        except ValueError:
            print(f"{RED}Invalid file size, please try again.{RESET}")

    while True:
        try:
            tcp_connections = int(input(f"{YELLOW}Enter number of TCP connections (e.g. 1): {RESET}"))
            if tcp_connections < 0:
                raise ValueError
            break
        except ValueError:
            print(f"{RED}Invalid number, please try again.{RESET}")

    while True:
        try:
            udp_connections = int(input(f"{YELLOW}Enter number of UDP connections (e.g. 1): {RESET}"))
            if udp_connections < 0:
                raise ValueError
            break
        except ValueError:
            print(f"{RED}Invalid number, please try again.{RESET}")

    # 2) Discover server
    server_ip, server_udp_port, server_tcp_port = udp_discover()
    if not server_ip:
        print(f"{RED}No server offer received. Exiting.{RESET}")
        return

    # 3) Perform speed test
    print(f"{CYAN}Starting speed test against server={server_ip}, "
          f"file_size={file_size}, TCP={tcp_connections}, UDP={udp_connections}{RESET}")
    perform_speed_test(
        server_ip,
        server_udp_port,
        server_tcp_port,
        file_size,
        tcp_connections,
        udp_connections
    )

    print(f"{GREEN}Client finished all transfers. Exiting.{RESET}")


# =============================================================================
#                                 MAIN
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"{RED}\nClient interrupted by user, exiting...{RESET}")
        client_shutdown_event.set()
