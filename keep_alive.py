# import threading
# import requests
# import time
# from pathlib import Path
# import config

# UPLOAD_DIR = Path("uploads")

# def keep_alive():
#     def ping():
#         while True:
#             try:
#                 # Always ping health first to keep server alive
#                 requests.get(f"{config.PUBLIC_URL}/health", timeout=5)
#                 print("[KeepAlive] Pinged")
                
#                 # Then try to warm audio separately
#                 try:
#                     from voice import synthesize_speech
#                     audio = synthesize_speech(
#                         "Namaste! Main Priya bol rahi hoon, Shubham Motors Hero MotoCorp se, Jaipur. Aap ka call receive karke bahut khushi hui! Kaise madad kar sakti hoon aapki?",
#                         "hinglish"
#                     )
#                     if audio:
#                         (UPLOAD_DIR / "opening_warmup.mp3").write_bytes(audio)
#                         print(f"[KeepAlive] Audio warmed ({len(audio)} bytes)")
#                 except Exception as e:
#                     print(f"[KeepAlive] Audio warmup failed: {e}")
                    
#             except Exception as e:
#                 print(f"[KeepAlive] Ping failed: {e}")
            
#             time.sleep(30)  # ping every 30 seconds

#     t = threading.Thread(target=ping, daemon=True)
#     t.start()


import threading
import requests
import time
import config

def keep_alive():
    def ping():
        while True:
            try:
                requests.get(f"{config.PUBLIC_URL}/health", timeout=5)
                print("[KeepAlive] Pinged")
            except Exception as e:
                print(f"[KeepAlive] Ping failed: {e}")
            time.sleep(30)

    t = threading.Thread(target=ping, daemon=True)
    t.start()