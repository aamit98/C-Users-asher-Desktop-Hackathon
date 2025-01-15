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
            print(f"Received raw data: {data} from {addr}")

        except socket.timeout:
            print(f"{YELLOW}No offers received yet... retrying.{RESET}")
            continue
        except KeyboardInterrupt:
            print(f"{RED}Client interrupted, shutting down...{RESET}")
            break

        try:
            magic_cookie, msg_type, udp_port, tcp_port = struct.unpack('!IBHH', data)
            print(
                f"Parsed packet: magic_cookie={hex(magic_cookie)}, msg_type={msg_type}, udp_port={udp_port}, tcp_port={tcp_port}")
            if magic_cookie == OFFER_MAGIC_COOKIE and msg_type == OFFER_MESSAGE_TYPE:
                print(f"{GREEN}Received offer from {addr[0]}:{RESET} UDP={udp_port}, TCP={tcp_port}")
                udp_socket.close()
                return addr[0], udp_port, tcp_port
        except Exception as e:
            print(f"{RED}Error parsing offer: {e}{RESET}")

    udp_socket.close()
    return None, None, None

def print_progress(identifier, current, total):
    """
    Shows a simple progress bar
    """
    bar_length = 20
    fraction = current / total if total > 0 else 1
    filled = int(bar_length * fraction)
    bar = '#' * filled + '-' * (bar_length - filled)
    print(f"\r{CYAN}[{identifier}] [{bar}] {current}/{total}{RESET}", end='', flush=True)


def perform_speed_test(server_ip, server_udp_port, server_tcp_port, file_size, tcp_connections, udp_connections):
    progress_dict = {}
    progress_lock = threading.Lock()

    def tcp_test(t_id):  # Parameter name updated to t_id
        retries = 3
        while retries > 0 and not client_shutdown_event.is_set():
            tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            start_time = time.time()
            bytes_received = 0
            data_buf = b""

            try:
                tcp_socket.settimeout(10)
                tcp_socket.connect((server_ip, server_tcp_port))
                tcp_socket.sendall(f"{file_size}\n".encode())

                chunk_size = 8192
                while bytes_received < file_size:
                    chunk = tcp_socket.recv(chunk_size)
                    if not chunk:
                        if bytes_received < file_size:
                            raise ConnectionError("Connection closed prematurely")
                        break
                    data_buf += chunk
                    bytes_received += len(chunk)

                    with progress_lock:
                        progress_dict[t_id] = bytes_received
                        print_progress(f"TCP-{t_id}", bytes_received, file_size)

                total_time = time.time() - start_time
                speed = bytes_received / total_time if total_time > 0 else 0
                print(
                    f"\n{GREEN}[TCP-{t_id}] Received {bytes_received} bytes in {total_time:.2f}s, speed={speed:.2f} B/s{RESET}")
                return

            except Exception as e:
                retries -= 1
                if retries > 0:
                    time.sleep(1)
                print(f"\n{RED}[TCP-{t_id}] Error: {e}, retries left: {retries}{RESET}")
            finally:
                tcp_socket.close()

    def udp_test(t_id):
        """
        Fixed UDP test client function
        """
        retries = 3
        while retries > 0 and not client_shutdown_event.is_set():
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Set buffer size
                udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
                udp_socket.settimeout(1)  # Shorter timeout
                thread_num = int(t_id[1:])  # Extract number from thread ID

                # Fix packet format to match server
                request_msg = struct.pack('!IBQQ',
                                          OFFER_MAGIC_COOKIE,
                                          REQUEST_MESSAGE_TYPE,
                                          file_size,
                                          thread_num)

                start_time = time.time()
                udp_socket.sendto(request_msg, (server_ip, server_udp_port))

                packets = {}
                arrival_times = []
                received_packets = 0
                last_update_time = time.time()
                no_data_count = 0
                max_no_data = 5

                while True:
                    try:
                        data, _ = udp_socket.recvfrom(2048)  # Increased buffer

                        # Fixed struct format to match server's response
                        header_size = struct.calcsize('!IBQQ')
                        if len(data) < header_size:
                            continue

                        cookie, msg_type, total, seq = struct.unpack('!IBQQ', data[:header_size])

                        if cookie != OFFER_MAGIC_COOKIE or msg_type != PAYLOAD_MESSAGE_TYPE:
                            continue

                        if seq not in packets:
                            packets[seq] = True
                            received_packets += 1
                            arrival_times.append(time.time())
                            no_data_count = 0

                        if time.time() - last_update_time >= 0.1:
                            with progress_lock:
                                progress_dict[t_id] = received_packets
                                expected_packets = file_size // 1024
                                print_progress(f"UDP-{t_id}", received_packets, expected_packets)
                            last_update_time = time.time()

                        if received_packets >= total:
                            break

                    except socket.timeout:
                        no_data_count += 1
                        if no_data_count > max_no_data:
                            break
                        continue

                total_time = time.time() - start_time
                expected_packets = file_size // 1024
                lost_packets = expected_packets - received_packets

                jitter = 0.0
                if len(arrival_times) > 1:
                    deltas = [abs(arrival_times[i] - arrival_times[i - 1]) for i in range(1, len(arrival_times))]
                    jitter = sum(deltas) / len(deltas) if deltas else 0

                speed = (received_packets * 1024) / total_time if total_time > 0 else 0

                print(f"\n{GREEN}[UDP-{t_id}] Received {received_packets}/{expected_packets} packets "
                      f"(loss={lost_packets}, jitter={jitter:.4f}s), speed={speed:.2f} B/s{RESET}")
                return

            except Exception as e:
                retries -= 1
                if retries > 0:
                    time.sleep(1)
                print(f"\n{RED}[UDP-{t_id}] Error: {e}, retries left: {retries}{RESET}")
            finally:
                try:
                    udp_socket.close()
                except:
                    pass

    threads = []
    for i in range(tcp_connections):
        t_id = f"T{i + 1}"
        progress_dict[t_id] = 0
        t = threading.Thread(target=tcp_test, args=(t_id,))
        t.start()
        threads.append(t)
        time.sleep(0.1)  # Stagger connection starts

    for i in range(udp_connections):
        t_id = f"U{i + 1}"
        progress_dict[t_id] = 0
        t = threading.Thread(target=udp_test, args=(t_id,))
        t.start()
        threads.append(t)
        time.sleep(0.1)  # Stagger connection starts

    for t in threads:
        t.join()

    print(f"{GREEN}All transfers completed!{RESET}")

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