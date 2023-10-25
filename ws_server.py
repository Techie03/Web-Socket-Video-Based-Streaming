import socket, cv2, pickle,struct
import threading
import time

 

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("8.8.8.8", 80))  # Google's public DNS server and port 80
host_ip = sock.getsockname()[0]  # Get the local IP address
port = 8002


socket_address = (host_ip,port)
command_address = (host_ip,port+1)


def send_video():
# Socket Accept
    while True:
        server_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)# Socket Create
        server_socket.bind(socket_address)# Socket Bind
        server_socket.listen(5)# Socket Listen
        print("LISTENING VIDEO AT:",socket_address)
        client_socket,addr = server_socket.accept()
        print('GOT CONNECTION FROM:',addr)
        if client_socket:
            vid = cv2.VideoCapture(0)
            while(vid.isOpened()):
                try:
                    img,frame = vid.read()
                    if img:
                        frame = cv2.resize(frame,(frame.shape[1]//2, frame.shape[0]//2))
                        a = pickle.dumps(frame)
                        message = struct.pack("Q",len(a))+a
                        client_socket.sendall(message)
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    print("ERROR:", e)
                    server_socket.close()
                    time.sleep(5)
                    break


# t1 = threading.Thread(target=get_command)
# t1.start()

send_video()
