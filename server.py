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
    """
    Handles a single TCP connection with a client.
    Receives an integer (file size), then returns that many bytes.
    """
    try:
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

        data = b'A' * file_size  # Mock data
        conn.sendall(data)

        print(f"{GREEN}[TCP] Finished sending {file_size} bytes to {addr}.{RESET}")

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
        server_socket.listen(5)
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
    Handles a single UDP request from the client:
      - (magic_cookie=0xabcddcba, msg_type=0x3, file_size=8 bytes).
    Replies with multiple payload packets.
    """
    try:
        if len(data) < 13:
            print(f"{RED}[UDP] Invalid packet from {addr}, too short.{RESET}")
            return

        magic_cookie, msg_type, file_size = struct.unpack('!IBQ', data)
        if magic_cookie != OFFER_MAGIC_COOKIE or msg_type != REQUEST_MESSAGE_TYPE:
            print(f"{RED}[UDP] Ignored packet from {addr}, bad cookie or msg_type.{RESET}")
            return

        print(f"{GREEN}[UDP] {addr} requested {file_size} bytes.{RESET}")
        total_segments = file_size // 1024

        for segment in range(total_segments):
            payload = struct.pack('!IBQQ',
                                  OFFER_MAGIC_COOKIE,
                                  PAYLOAD_MESSAGE_TYPE,
                                  total_segments,
                                  segment) + b'A' * 1024
            udp_socket.sendto(payload, addr)
            time.sleep(0.0005)  # slightly faster

        print(f"{GREEN}[UDP] Finished sending {total_segments} UDP segments to {addr}.{RESET}")

    except Exception as e:
        print(f"{RED}[UDP Error] with {addr}: {e}{RESET}")
    # Do NOT close udp_socket here (it’s shared by the whole server!)


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
