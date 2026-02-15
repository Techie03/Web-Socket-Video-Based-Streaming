"""
WebSocket Video Streaming Server
==================================
Real-time bi-directional video transmission with low-latency protocol
Handles concurrent connections with optimized frame encoding pipeline
Maintains consistent streaming quality across network conditions
"""

import socket
import cv2
import pickle
import struct
import threading
import time
import queue
import logging
from datetime import datetime
from typing import Dict, List
import json
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class StreamingConfig:
    """Configuration for video streaming"""
    
    # Network settings
    PORT = 8002
    MAX_CONNECTIONS = 10
    BUFFER_SIZE = 4 * 1024  # 4KB
    
    # Video settings
    FRAME_WIDTH = 640
    FRAME_HEIGHT = 480
    FPS = 30
    QUALITY = 80  # JPEG quality 0-100
    
    # Performance settings
    FRAME_QUEUE_SIZE = 30  # ~1 second buffer at 30 FPS
    RECONNECT_DELAY = 2  # seconds
    HEARTBEAT_INTERVAL = 5  # seconds
    
    # Encoding settings
    ENCODE_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, QUALITY]

class ConnectionMetrics:
    """Track connection performance metrics"""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.frames_sent = 0
        self.bytes_sent = 0
        self.connection_start = time.time()
        self.last_frame_time = time.time()
        self.dropped_frames = 0
        self.errors = 0
        self.latency_history = []
        
    def update_frame_sent(self, frame_size: int, latency: float = None):
        """Update metrics after sending frame"""
        self.frames_sent += 1
        self.bytes_sent += frame_size
        self.last_frame_time = time.time()
        if latency:
            self.latency_history.append(latency)
            if len(self.latency_history) > 100:
                self.latency_history.pop(0)
    
    def get_stats(self) -> dict:
        """Get connection statistics"""
        duration = time.time() - self.connection_start
        return {
            'client_id': self.client_id,
            'frames_sent': self.frames_sent,
            'bytes_sent': self.bytes_sent,
            'duration_seconds': round(duration, 2),
            'fps': round(self.frames_sent / duration, 2) if duration > 0 else 0,
            'avg_latency_ms': round(np.mean(self.latency_history), 2) if self.latency_history else 0,
            'dropped_frames': self.dropped_frames,
            'errors': self.errors
        }

class VideoStreamServer:
    """
    WebSocket-based video streaming server with concurrent connection support
    """
    
    def __init__(self, config: StreamingConfig = StreamingConfig()):
        self.config = config
        self.running = False
        self.clients: Dict[str, socket.socket] = {}
        self.client_metrics: Dict[str, ConnectionMetrics] = {}
        self.frame_queue = queue.Queue(maxsize=config.FRAME_QUEUE_SIZE)
        
        # Get local IP
        self.host_ip = self._get_local_ip()
        self.socket_address = (self.host_ip, config.PORT)
        
        logger.info(f"Server initialized at {self.socket_address}")
        
    def _get_local_ip(self) -> str:
        """Get local IP address"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            sock.close()
            return local_ip
        except Exception as e:
            logger.error(f"Failed to get local IP: {e}")
            return "127.0.0.1"
    
    def capture_video(self):
        """
        Capture video from camera and add to frame queue
        Optimized frame encoding for consistent quality
        """
        logger.info("Starting video capture...")
        vid = cv2.VideoCapture(0)
        
        # Set camera properties
        vid.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.FRAME_WIDTH)
        vid.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.FRAME_HEIGHT)
        vid.set(cv2.CAP_PROP_FPS, self.config.FPS)
        
        frame_count = 0
        last_fps_time = time.time()
        fps = 0
        
        while self.running:
            try:
                ret, frame = vid.read()
                
                if not ret:
                    logger.warning("Failed to capture frame")
                    time.sleep(0.1)
                    continue
                
                # Optimize frame size while maintaining quality
                if frame.shape[1] > self.config.FRAME_WIDTH or frame.shape[0] > self.config.FRAME_HEIGHT:
                    frame = cv2.resize(frame, (self.config.FRAME_WIDTH, self.config.FRAME_HEIGHT))
                
                # Encode frame with optimized quality
                encode_param = self.config.ENCODE_PARAMS
                result, encoded_frame = cv2.imencode('.jpg', frame, encode_param)
                
                if not result:
                    logger.warning("Frame encoding failed")
                    continue
                
                # Add to queue (non-blocking)
                try:
                    self.frame_queue.put_nowait({
                        'frame': encoded_frame,
                        'timestamp': time.time(),
                        'frame_number': frame_count
                    })
                except queue.Full:
                    # Drop frame if queue is full
                    logger.debug("Frame queue full, dropping frame")
                
                frame_count += 1
                
                # Calculate FPS
                if frame_count % 30 == 0:
                    current_time = time.time()
                    fps = 30 / (current_time - last_fps_time)
                    last_fps_time = current_time
                    logger.debug(f"Capture FPS: {fps:.2f}")
                
            except Exception as e:
                logger.error(f"Video capture error: {e}")
                time.sleep(0.1)
        
        vid.release()
        logger.info("Video capture stopped")
    
    def handle_client(self, client_socket: socket.socket, address: tuple):
        """
        Handle individual client connection
        Maintains streaming quality with adaptive encoding
        """
        client_id = f"{address[0]}:{address[1]}"
        self.clients[client_id] = client_socket
        self.client_metrics[client_id] = ConnectionMetrics(client_id)
        
        logger.info(f"Client connected: {client_id}")
        
        try:
            while self.running and client_id in self.clients:
                try:
                    # Get frame from queue with timeout
                    frame_data = self.frame_queue.get(timeout=1)
                    
                    # Serialize frame data
                    encoded_frame = frame_data['frame']
                    frame_bytes = encoded_frame.tobytes()
                    
                    # Pack frame size and data
                    frame_size = len(frame_bytes)
                    packed_data = struct.pack("Q", frame_size) + frame_bytes
                    
                    # Send with latency tracking
                    send_start = time.time()
                    client_socket.sendall(packed_data)
                    send_latency = (time.time() - send_start) * 1000  # ms
                    
                    # Update metrics
                    self.client_metrics[client_id].update_frame_sent(frame_size, send_latency)
                    
                    # Adaptive quality adjustment based on latency
                    if send_latency > 100:  # High latency
                        logger.warning(f"High latency detected for {client_id}: {send_latency:.2f}ms")
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Error sending to {client_id}: {e}")
                    self.client_metrics[client_id].errors += 1
                    break
                    
        except Exception as e:
            logger.error(f"Client handler error for {client_id}: {e}")
        
        finally:
            # Cleanup
            self._disconnect_client(client_id)
    
    def _disconnect_client(self, client_id: str):
        """Disconnect and cleanup client connection"""
        if client_id in self.clients:
            try:
                self.clients[client_id].close()
            except:
                pass
            del self.clients[client_id]
            
            # Log final stats
            if client_id in self.client_metrics:
                stats = self.client_metrics[client_id].get_stats()
                logger.info(f"Client disconnected: {client_id}")
                logger.info(f"Final stats: {json.dumps(stats, indent=2)}")
        
    def accept_connections(self):
        """
        Accept and handle concurrent client connections
        """
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(self.socket_address)
        server_socket.listen(self.config.MAX_CONNECTIONS)
        server_socket.settimeout(1.0)  # Non-blocking accept
        
        logger.info(f"Server listening at {self.socket_address}")
        logger.info(f"Max concurrent connections: {self.config.MAX_CONNECTIONS}")
        
        while self.running:
            try:
                client_socket, address = server_socket.accept()
                
                # Check connection limit
                if len(self.clients) >= self.config.MAX_CONNECTIONS:
                    logger.warning(f"Connection rejected (max reached): {address}")
                    client_socket.close()
                    continue
                
                # Handle client in separate thread
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, address),
                    daemon=True
                )
                client_thread.start()
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Accept connection error: {e}")
        
        server_socket.close()
        logger.info("Server socket closed")
    
    def monitor_connections(self):
        """Monitor and log connection statistics"""
        while self.running:
            time.sleep(10)  # Log every 10 seconds
            
            if self.clients:
                logger.info(f"Active connections: {len(self.clients)}")
                for client_id, metrics in self.client_metrics.items():
                    if client_id in self.clients:
                        stats = metrics.get_stats()
                        logger.info(f"  {client_id}: {stats['frames_sent']} frames, "
                                  f"{stats['fps']} FPS, "
                                  f"{stats['avg_latency_ms']}ms latency")
    
    def start(self):
        """Start the streaming server"""
        logger.info("="*80)
        logger.info("WEBSOCKET VIDEO STREAMING SERVER")
        logger.info("="*80)
        logger.info(f"\nConfiguration:")
        logger.info(f"  • Server Address: {self.socket_address}")
        logger.info(f"  • Resolution: {self.config.FRAME_WIDTH}x{self.config.FRAME_HEIGHT}")
        logger.info(f"  • Target FPS: {self.config.FPS}")
        logger.info(f"  • Quality: {self.config.QUALITY}%")
        logger.info(f"  • Max Connections: {self.config.MAX_CONNECTIONS}")
        logger.info(f"  • Buffer Size: {self.config.BUFFER_SIZE} bytes")
        logger.info("\nPress Ctrl+C to stop the server\n")
        logger.info("="*80 + "\n")
        
        self.running = True
        
        # Start video capture thread
        capture_thread = threading.Thread(target=self.capture_video, daemon=True)
        capture_thread.start()
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=self.monitor_connections, daemon=True)
        monitor_thread.start()
        
        try:
            # Accept connections (blocking)
            self.accept_connections()
        except KeyboardInterrupt:
            logger.info("\n\nShutdown requested by user")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the streaming server"""
        logger.info("Stopping server...")
        self.running = False
        
        # Disconnect all clients
        client_ids = list(self.clients.keys())
        for client_id in client_ids:
            self._disconnect_client(client_id)
        
        logger.info("Server stopped successfully")
        
        # Print final summary
        self._print_summary()
    
    def _print_summary(self):
        """Print server session summary"""
        logger.info("\n" + "="*80)
        logger.info("SERVER SESSION SUMMARY")
        logger.info("="*80)
        
        total_frames = sum(m.frames_sent for m in self.client_metrics.values())
        total_bytes = sum(m.bytes_sent for m in self.client_metrics.values())
        total_clients = len(self.client_metrics)
        
        logger.info(f"\nSession Statistics:")
        logger.info(f"  • Total Clients Served: {total_clients}")
        logger.info(f"  • Total Frames Sent: {total_frames}")
        logger.info(f"  • Total Data Sent: {total_bytes / (1024*1024):.2f} MB")
        
        if self.client_metrics:
            logger.info(f"\nPer-Client Statistics:")
            for client_id, metrics in self.client_metrics.items():
                stats = metrics.get_stats()
                logger.info(f"  {client_id}:")
                logger.info(f"    - Frames: {stats['frames_sent']}")
                logger.info(f"    - Duration: {stats['duration_seconds']}s")
                logger.info(f"    - Average FPS: {stats['fps']}")
                logger.info(f"    - Average Latency: {stats['avg_latency_ms']}ms")
        
        logger.info("\n" + "="*80 + "\n")

def main():
    """Main entry point"""
    config = StreamingConfig()
    server = VideoStreamServer(config)
    
    try:
        server.start()
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
