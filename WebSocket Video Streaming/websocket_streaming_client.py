"""
WebSocket Video Streaming Client
==================================
Receives real-time video stream with low-latency bi-directional communication
Monitors streaming quality and handles network condition variations
"""

import socket
import cv2
import pickle
import struct
import time
import threading
import logging
import numpy as np
from collections import deque
from datetime import datetime
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ClientConfig:
    """Client configuration"""
    PORT = 8002
    BUFFER_SIZE = 4 * 1024  # 4KB
    RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAY = 2  # seconds
    STATS_DISPLAY_INTERVAL = 30  # frames

class ClientMetrics:
    """Track client-side performance metrics"""
    
    def __init__(self):
        self.frames_received = 0
        self.bytes_received = 0
        self.start_time = time.time()
        self.last_frame_time = time.time()
        self.frame_latencies = deque(maxlen=100)
        self.dropped_frames = 0
        self.reconnections = 0
        self.errors = 0
        
        # FPS calculation
        self.fps_counter = deque(maxlen=30)
        self.last_fps_time = time.time()
        
    def update_frame_received(self, frame_size: int, receive_latency: float = None):
        """Update metrics after receiving frame"""
        current_time = time.time()
        
        self.frames_received += 1
        self.bytes_received += frame_size
        
        # Calculate frame interval (for dropped frame detection)
        frame_interval = current_time - self.last_frame_time
        if frame_interval > 0.05:  # >50ms gap suggests dropped frames
            estimated_drops = int((frame_interval - 0.033) / 0.033)  # Assuming 30 FPS
            self.dropped_frames += max(0, estimated_drops)
        
        self.last_frame_time = current_time
        
        # Track latency
        if receive_latency:
            self.frame_latencies.append(receive_latency)
        
        # FPS calculation
        self.fps_counter.append(current_time)
    
    def get_fps(self) -> float:
        """Calculate current FPS"""
        if len(self.fps_counter) < 2:
            return 0.0
        time_diff = self.fps_counter[-1] - self.fps_counter[0]
        return len(self.fps_counter) / time_diff if time_diff > 0 else 0.0
    
    def get_stats(self) -> dict:
        """Get comprehensive statistics"""
        duration = time.time() - self.start_time
        return {
            'frames_received': self.frames_received,
            'bytes_received': self.bytes_received,
            'duration_seconds': round(duration, 2),
            'average_fps': round(self.frames_received / duration, 2) if duration > 0 else 0,
            'current_fps': round(self.get_fps(), 2),
            'average_latency_ms': round(np.mean(self.frame_latencies), 2) if self.frame_latencies else 0,
            'dropped_frames': self.dropped_frames,
            'reconnections': self.reconnections,
            'errors': self.errors,
            'data_rate_mbps': round((self.bytes_received * 8) / (duration * 1_000_000), 2) if duration > 0 else 0
        }

class VideoStreamClient:
    """
    WebSocket video streaming client with quality monitoring
    """
    
    def __init__(self, server_ip: str = None, config: ClientConfig = ClientConfig()):
        self.config = config
        self.metrics = ClientMetrics()
        self.running = False
        self.connected = False
        self.client_socket = None
        
        # Get server IP (use local IP if not specified)
        if server_ip is None:
            self.server_ip = self._get_local_ip()
        else:
            self.server_ip = server_ip
        
        self.server_address = (self.server_ip, config.PORT)
        
        logger.info(f"Client initialized for server: {self.server_address}")
    
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
    
    def connect(self) -> bool:
        """Connect to streaming server"""
        try:
            logger.info(f"Connecting to server at {self.server_address}...")
            
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.settimeout(5.0)
            self.client_socket.connect(self.server_address)
            
            self.connected = True
            logger.info("✓ Connected successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.connected = False
            return False
    
    def reconnect(self) -> bool:
        """Attempt to reconnect to server"""
        logger.info("Attempting to reconnect...")
        
        for attempt in range(1, self.config.RECONNECT_ATTEMPTS + 1):
            logger.info(f"Reconnection attempt {attempt}/{self.config.RECONNECT_ATTEMPTS}")
            
            if self.connect():
                self.metrics.reconnections += 1
                return True
            
            if attempt < self.config.RECONNECT_ATTEMPTS:
                time.sleep(self.config.RECONNECT_DELAY)
        
        logger.error("Reconnection failed after all attempts")
        return False
    
    def receive_frame(self) -> np.ndarray:
        """
        Receive a single frame from server
        Returns decoded frame or None if error
        """
        try:
            # Receive payload size
            payload_size = struct.calcsize("Q")
            data = b""
            
            receive_start = time.time()
            
            while len(data) < payload_size:
                packet = self.client_socket.recv(self.config.BUFFER_SIZE)
                if not packet:
                    logger.warning("No data received (connection closed)")
                    return None
                data += packet
            
            # Unpack message size
            packed_msg_size = data[:payload_size]
            data = data[payload_size:]
            msg_size = struct.unpack("Q", packed_msg_size)[0]
            
            # Receive full frame data
            while len(data) < msg_size:
                packet = self.client_socket.recv(self.config.BUFFER_SIZE)
                if not packet:
                    logger.warning("Incomplete frame data received")
                    return None
                data += packet
            
            # Extract frame data
            frame_data = data[:msg_size]
            receive_latency = (time.time() - receive_start) * 1000  # ms
            
            # Decode frame
            frame_array = np.frombuffer(frame_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
            
            if frame is not None:
                self.metrics.update_frame_received(msg_size, receive_latency)
            
            return frame
            
        except socket.timeout:
            logger.warning("Frame receive timeout")
            return None
        except Exception as e:
            logger.error(f"Error receiving frame: {e}")
            self.metrics.errors += 1
            return None
    
    def display_stats(self, frame: np.ndarray) -> np.ndarray:
        """Overlay statistics on frame"""
        stats = self.metrics.get_stats()
        
        # Create stats overlay
        overlay_height = 140
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], overlay_height), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
        
        # Display stats
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        color = (0, 255, 0)
        thickness = 2
        
        y_offset = 25
        line_height = 25
        
        stats_text = [
            f"FPS: {stats['current_fps']:.1f} (Avg: {stats['average_fps']:.1f})",
            f"Latency: {stats['average_latency_ms']:.1f}ms",
            f"Frames: {stats['frames_received']} (Dropped: {stats['dropped_frames']})",
            f"Data Rate: {stats['data_rate_mbps']:.2f} Mbps",
            f"Duration: {stats['duration_seconds']:.1f}s"
        ]
        
        for i, text in enumerate(stats_text):
            cv2.putText(frame, text, (10, y_offset + i * line_height), 
                       font, font_scale, color, thickness)
        
        # Connection status
        status_text = "CONNECTED" if self.connected else "DISCONNECTED"
        status_color = (0, 255, 0) if self.connected else (0, 0, 255)
        cv2.putText(frame, status_text, (frame.shape[1] - 200, 30), 
                   font, 0.7, status_color, 2)
        
        return frame
    
    def stream(self):
        """
        Main streaming loop - receive and display frames
        """
        logger.info("="*80)
        logger.info("WEBSOCKET VIDEO STREAMING CLIENT")
        logger.info("="*80)
        logger.info(f"\nServer: {self.server_address}")
        logger.info("\nControls:")
        logger.info("  • Press 'q' to quit")
        logger.info("  • Press 's' to show/hide statistics")
        logger.info("  • Press 'r' to save current frame")
        logger.info("\n" + "="*80 + "\n")
        
        self.running = True
        show_stats = True
        frame_count = 0
        
        try:
            while self.running:
                if not self.connected:
                    if not self.reconnect():
                        logger.error("Failed to establish connection. Exiting...")
                        break
                
                # Receive frame
                frame = self.receive_frame()
                
                if frame is None:
                    logger.warning("No frame received, checking connection...")
                    self.connected = False
                    continue
                
                frame_count += 1
                
                # Add statistics overlay
                if show_stats:
                    frame = self.display_stats(frame)
                
                # Display frame
                cv2.imshow("WebSocket Video Stream", frame)
                
                # Log stats periodically
                if frame_count % self.config.STATS_DISPLAY_INTERVAL == 0:
                    stats = self.metrics.get_stats()
                    logger.info(f"Streaming: {stats['frames_received']} frames, "
                              f"{stats['current_fps']:.1f} FPS, "
                              f"{stats['average_latency_ms']:.1f}ms latency")
                
                # Handle keyboard input
                key = cv2.waitKey(1) & 0xFF
                
                if key == ord('q'):
                    logger.info("Quit requested by user")
                    break
                elif key == ord('s'):
                    show_stats = not show_stats
                    logger.info(f"Statistics display: {'ON' if show_stats else 'OFF'}")
                elif key == ord('r'):
                    # Save frame
                    filename = f"frame_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                    cv2.imwrite(filename, frame)
                    logger.info(f"Frame saved as {filename}")
                    
        except KeyboardInterrupt:
            logger.info("\nInterrupted by user")
        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
        finally:
            self.stop()
    
    def stop(self):
        """Stop the client and cleanup"""
        logger.info("\nStopping client...")
        self.running = False
        self.connected = False
        
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
        
        cv2.destroyAllWindows()
        
        # Print final statistics
        self._print_summary()
        
        logger.info("Client stopped successfully")
    
    def _print_summary(self):
        """Print session summary"""
        stats = self.metrics.get_stats()
        
        logger.info("\n" + "="*80)
        logger.info("CLIENT SESSION SUMMARY")
        logger.info("="*80)
        
        logger.info(f"\nPerformance Metrics:")
        logger.info(f"  • Frames Received: {stats['frames_received']}")
        logger.info(f"  • Dropped Frames: {stats['dropped_frames']}")
        logger.info(f"  • Data Received: {stats['bytes_received'] / (1024*1024):.2f} MB")
        logger.info(f"  • Average FPS: {stats['average_fps']:.2f}")
        logger.info(f"  • Average Latency: {stats['average_latency_ms']:.2f} ms")
        logger.info(f"  • Data Rate: {stats['data_rate_mbps']:.2f} Mbps")
        logger.info(f"  • Duration: {stats['duration_seconds']:.2f} seconds")
        logger.info(f"  • Reconnections: {stats['reconnections']}")
        logger.info(f"  • Errors: {stats['errors']}")
        
        # Quality assessment
        quality = "Excellent"
        if stats['dropped_frames'] > stats['frames_received'] * 0.05:
            quality = "Poor"
        elif stats['dropped_frames'] > stats['frames_received'] * 0.01:
            quality = "Fair"
        elif stats['average_latency_ms'] > 100:
            quality = "Good"
        
        logger.info(f"\n  Overall Quality: {quality}")
        
        logger.info("\n" + "="*80 + "\n")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='WebSocket Video Streaming Client')
    parser.add_argument('--host', type=str, default=None,
                       help='Server IP address (default: auto-detect local IP)')
    parser.add_argument('--port', type=int, default=8002,
                       help='Server port (default: 8002)')
    
    args = parser.parse_args()
    
    config = ClientConfig()
    config.PORT = args.port
    
    client = VideoStreamClient(server_ip=args.host, config=config)
    
    try:
        if client.connect():
            client.stream()
        else:
            logger.error("Failed to connect to server")
    except Exception as e:
        logger.error(f"Client error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
