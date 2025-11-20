@echo off
setlocal enabledelayedexpansion

cd /d C:\Users\blyth\Desktop\Engineering\Apollo

REM Apollo-specific models
set APOLLO_GENERATION_MODEL=Fino1-8B.Q6_K
set APOLLO_EMBEDDING_MODEL=nomic-embed-text

REM Optional: explicit Ollama host if needed
REM set OLLAMA_HOST=http://127.0.0.1:11434

echo.
echo ======================================================================
echo Apollo RAG Quick Check
echo ======================================================================
echo.
echo Configuration:
echo   APOLLO_GENERATION_MODEL=!APOLLO_GENERATION_MODEL!
echo   APOLLO_EMBEDDING_MODEL=!APOLLO_EMBEDDING_MODEL!
echo.

python rag_sanity_test.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Quick check completed successfully.
) else (
    echo.
    echo Quick check failed. See errors above.
)

exit /b %ERRORLEVEL%
