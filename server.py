import socket
import struct
import threading
import time

# =============================================================================
#                               CONSTANTS
# =============================================================================
connection_lock = threading.Lock()
OFFER_MAGIC_COOKIE = 0xabcddcba
OFFER_MESSAGE_TYPE = 0x2
REQUEST_MESSAGE_TYPE = 0x3
PAYLOAD_MESSAGE_TYPE = 0x4

UDP_BROADCAST_PORT = 13117   # Where the server sends broadcast offers
UDP_SERVER_PORT = 20001      # Where the server listens for UDP requests
TCP_SERVER_PORT = 20002      # Where the server listens for TCP connections

# Event to signal the server threads to shut down gracefully
server_shutdown_event = threading.Event()

# For colorful printing
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

# =============================================================================
#                               SERVER CODE
# =============================================================================

def udp_offer_broadcast():
    """
    Broadcasts offer messages over UDP until 'server_shutdown_event' is set.
    """
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    # Enable broadcast
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    offer_message = struct.pack('!IBHH',
                                OFFER_MAGIC_COOKIE,
                                OFFER_MESSAGE_TYPE,
                                UDP_SERVER_PORT,
                                TCP_SERVER_PORT)

    try:
        while not server_shutdown_event.is_set():
            udp_socket.sendto(offer_message, ('172.20.10.15', UDP_BROADCAST_PORT))
            time.sleep(1)
    finally:
        udp_socket.close()


def handle_client_tcp(conn, addr):
    with connection_lock:  # Add connection_lock = threading.Lock() at top of file
        current_conn = threading.active_count()

    try:
        conn.settimeout(30)
        raw_data = conn.recv(1024)
        if not raw_data:
            print(f"{RED}[TCP] Received no data from {addr}, closing.{RESET}")
            return

        file_size_str = raw_data.decode(errors='ignore').strip()
        if not file_size_str.isdigit():
            print(f"{RED}[TCP] Invalid file size from {addr}: '{file_size_str}'{RESET}")
            return

        file_size = int(file_size_str)
        print(f"{GREEN}[TCP] {addr} requested {file_size} bytes.{RESET}")

        chunk_size = 8192
        bytes_sent = 0
        while bytes_sent < file_size and not server_shutdown_event.is_set():
            remaining = file_size - bytes_sent
            chunk = min(chunk_size, remaining)
            conn.sendall(b'A' * chunk)
            bytes_sent += chunk

        print(f"{GREEN}[TCP] Finished sending {bytes_sent} bytes to {addr}.{RESET}")

    except Exception as e:
        print(f"{RED}[TCP Error] with {addr}: {e}{RESET}")
    finally:
        conn.close()


def tcp_server():
    """
    Listens for incoming TCP connections on 'TCP_SERVER_PORT'.
    Spawns a new thread to handle each client.
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        server_socket.bind(("", TCP_SERVER_PORT))
        server_socket.listen(1000)
        server_socket.settimeout(1)
        print(f"{YELLOW}TCP Server started, listening on port {TCP_SERVER_PORT}{RESET}")

        while not server_shutdown_event.is_set():
            try:
                conn, addr = server_socket.accept()
            except socket.timeout:
                continue

            threading.Thread(
                target=handle_client_tcp,
                args=(conn, addr),
                daemon=True
            ).start()

    except OSError as e:
        print(f"{RED}[TCP] Could not bind on port {TCP_SERVER_PORT}: {e}{RESET}")
    finally:
        server_socket.close()


def handle_client_udp(data, addr, udp_socket):
    """
    Fixed UDP handler with proper socket management and error handling
    """
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)

        # Changed format to match client (!IBQQ)
        magic_cookie, msg_type, file_size, thread_id = struct.unpack('!IBQQ', data)
        if magic_cookie != OFFER_MAGIC_COOKIE or msg_type != REQUEST_MESSAGE_TYPE:
            return

        print(f"{GREEN}[UDP] {addr} requested {file_size} bytes (thread {thread_id}).{RESET}")
        total_segments = file_size // 1024
        sent_segments = 0

        client_socket.settimeout(30)

        for segment in range(total_segments):
            if server_shutdown_event.is_set():
                break

            try:
                # Changed format to match client (!IBQQ)
                payload = struct.pack('!IBQQ',
                                    OFFER_MAGIC_COOKIE,
                                    PAYLOAD_MESSAGE_TYPE,
                                    total_segments,
                                    segment)
                payload += b'A' * (1024 - len(payload))

                client_socket.sendto(payload, addr)
                sent_segments += 1

                if sent_segments % 50 == 0:
                    time.sleep(0.01)

            except (socket.error, socket.timeout) as e:
                print(f"{RED}[UDP] Error sending to {addr}, segment {segment}/{total_segments}: {e}{RESET}")
                time.sleep(0.05)
                continue

        print(f"{GREEN}[UDP] Finished sending {sent_segments}/{total_segments} segments to {addr}.{RESET}")

    except Exception as e:
        print(f"{RED}[UDP Error] with {addr}: {e}{RESET}")
    finally:
        client_socket.close()

def udp_server():
    """
    Listens on 'UDP_SERVER_PORT' for client requests.
    Spawns a thread to handle each incoming request (does not close the socket per-request).
    """
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        udp_socket.bind(("", UDP_SERVER_PORT))
        udp_socket.settimeout(1)
        print(f"{YELLOW}UDP Server started, listening on port {UDP_SERVER_PORT}{RESET}")

        while not server_shutdown_event.is_set():
            try:
                data, addr = udp_socket.recvfrom(2048)
            except socket.timeout:
                continue

            threading.Thread(
                target=handle_client_udp,
                args=(data, addr, udp_socket),
                daemon=True
            ).start()

    except OSError as e:
        print(f"{RED}[UDP] Could not bind on port {UDP_SERVER_PORT}: {e}{RESET}")
    finally:
        udp_socket.close()



# =============================================================================
#                                 MAIN
# =============================================================================

if __name__ == "__main__":
    print(f"{GREEN}Running Server...{RESET}")

    server_shutdown_event.clear()

    # Start broadcasting offers
    t_broadcast = threading.Thread(target=udp_offer_broadcast, daemon=True)
    # Start TCP server
    t_tcp = threading.Thread(target=tcp_server, daemon=True)
    # Start UDP server
    t_udp = threading.Thread(target=udp_server, daemon=True)

    t_broadcast.start()
    t_tcp.start()
    t_udp.start()

    print(f"{YELLOW}Press Ctrl+C to stop the server.{RESET}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"{RED}\nStopping server...{RESET}")

    # Signal all threads to shut down
    server_shutdown_event.set()

    # Optionally join threads
    t_broadcast.join()
    t_tcp.join()
    t_udp.join()

    print(f"{GREEN}Server stopped.{RESET}")
