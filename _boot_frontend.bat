@echo off
set "PATH=C:\Program Files\nodejs;%PATH%"
cd /d d:\Download\UniLabOS-new\frontend
set "VITE_EDGE_API_URL=http://127.0.0.1:59391"
npm run dev -- --host 127.0.0.1 --port 4174
