@echo off
echo ============================================================
echo  Installing MP3 to SRT Transcriber Dependencies
echo ============================================================
echo.

echo [1/3] Installing faster-whisper...
pip install faster-whisper
if %errorlevel% neq 0 (
    echo ERROR: faster-whisper install failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing pyannote.audio (speaker diarization)...
pip install pyannote.audio
if %errorlevel% neq 0 (
    echo ERROR: pyannote.audio install failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Installing torchaudio (audio backend for pyannote)...
pip install torchaudio
if %errorlevel% neq 0 (
    echo WARNING: torchaudio install failed - some features may not work.
)

echo.
echo ============================================================
echo  All dependencies installed successfully.
echo.
echo  NEXT STEPS before running the tool:
echo.
echo  1. Create a free HuggingFace account at huggingface.co
echo  2. Go to Settings ^> Access Tokens ^> New Token (read access)
echo  3. Accept the model terms at these two URLs (must be logged in):
echo     https://huggingface.co/pyannote/speaker-diarization-3.1
echo     https://huggingface.co/pyannote/segmentation-3.0
echo.
echo  Then run: python mp3_to_srt_transcriber.py
echo ============================================================
echo.
pause
