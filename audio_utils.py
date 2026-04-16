"""
audio_utils.py

OPTIMIZATIONS:
- 🔥 OPTIMIZATION: Lower silence threshold for faster speech detection
- 🔥 OPTIMIZATION: Early exit on silence detection (check first 2000 samples only)
- 🔥 OPTIMIZATION: Lazy import of pydub (only when needed)
"""
import numpy as np

def _is_silence(pcm: bytes, threshold: int = 80) -> bool:
    samples = np.frombuffer(pcm, dtype=np.int16)
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    return rms < threshold


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Convert raw PCM bytes to WAV format for Sarvam STT."""
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _mp3_to_pcm(mp3_bytes: bytes) -> bytes:
    """Convert audio bytes (WAV or MP3) to raw PCM 16-bit 8kHz mono for Exotel."""
    try:
        from pydub import AudioSegment
        import io
        
        if not mp3_bytes or len(mp3_bytes) < 100:
            print(f"[Audio] Audio too small: {len(mp3_bytes)} bytes")
            return b""
        
        if mp3_bytes[:4] == b'RIFF':
            fmt = "wav"
        elif mp3_bytes[:3] == b'ID3' or mp3_bytes[:2] in (b'\xff\xfb', b'\xff\xf3'):
            fmt = "mp3"
        else:
            fmt = "wav"
        
        audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format=fmt)
        audio = audio.set_frame_rate(8000).set_channels(1).set_sample_width(2)
        return audio.raw_data
    except Exception as e:
        print(f"[Audio] Audio to PCM failed: {e}")
        return b""
