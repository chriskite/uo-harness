@echo off
pktmon stop
pktmon etl2pcap C:\Users\chris\uo-harness\capture_2593.etl --out C:\Users\chris\uo-harness\capture_2593.pcapng
pktmon filter remove
echo stopped > C:\Users\chris\uo-harness\capture_state.txt
