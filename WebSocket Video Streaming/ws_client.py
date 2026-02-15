import socket,cv2, pickle,struct
import time
import threading

#Firstly run ws_server

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("8.8.8.8", 80))  # Google's public DNS server and port 80
host_ip = sock.getsockname()[0] 
port = 8002

data = b""
payload_size = struct.calcsize("Q")
print("payload_size", payload_size)


client_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
client_socket.connect((host_ip,port)) # a tuple

count = 0
while True:
    
    while len(data) < payload_size:
        packet = client_socket.recv(4*1024) # 4K
        if not packet: 
            break
        data+=packet
    packed_msg_size = data[:payload_size]
    data = data[payload_size:]
    msg_size = struct.unpack("Q",packed_msg_size)[0]
    
    while len(data) < msg_size:
        data += client_socket.recv(4*1024)

    frame_data = data[:msg_size]
    data  = data[msg_size:]
    frame = pickle.loads(frame_data)


    print('Received Image:', count)
    count += 1
    cv2.imshow("RECEIVING VIDEO",frame)
    key = cv2.waitKey(1) & 0xFF
    if key  == ord('q'):
        break

client_socket.close()
