#!/usr/bin/bash -l

# Script to automate the process of running the job aggregator
conda activate textscrape
sleep 2
python3 scripts/scraper.py --source manual
sleep 2
python3 scripts/merge_data.py
sleep 2
printf "Please open web browser at http://localhost:8000 \n"
printf "For default search open: http://localhost:8000/?title=data+%7C+computational+%7C+bioinformatician+%7C+bioinformatics+%7C+informatics+%7C+stat+%7C+statistics+%7C+programming+%7C+programmer+%7C+scientist+%7C+analyst+%7C+scientific+software+engineer+%7C+research+software+engineer+%7C+scientific+solutions+engineer+%7C+pipeline+developer \n"
sleep 2
python -m http.server 8000
bash