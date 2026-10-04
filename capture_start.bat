@echo off
pktmon filter remove
pktmon filter add UO2593 -t TCP -p 2593
pktmon start --capture --comp nics --pkt-size 0 --file-name "%~dp0capture_2593.etl"
echo started > "%~dp0capture_state.txt"
