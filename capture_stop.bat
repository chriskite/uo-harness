@echo off
pktmon stop
pktmon etl2pcap "%~dp0capture_2593.etl" --out "%~dp0capture_2593.pcapng"
pktmon filter remove
echo stopped > "%~dp0capture_state.txt"
