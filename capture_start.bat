@echo off
pktmon filter remove
pktmon filter add UO2593 -t TCP -p 2593
pktmon start --capture --comp nics --pkt-size 0 --file-name C:\Users\chris\uo-harness\capture_2593.etl
echo started > C:\Users\chris\uo-harness\capture_state.txt
