#!/usr/bin/bash -l

# Script to automate the process of running the job aggregator
conda activate textscrape
sleep 2
python3 scripts/scraper.py --source manual
sleep 2
python3 scripts/merge_data.py
sleep 2
printf "Please open web browser at http://localhost:8000 \n"
sleep 2
python -m http.server 8000
bash