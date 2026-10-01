#!/usr/bin/bash -l

# Script to automate the process of running the job aggregator
conda activate jobagg
sleep 2
python3 scripts/scraper.py --source manual
sleep 2
python3 scripts/merge_data.py
sleep 2
printf "Please open web browser at http://localhost:8000 \n"
sleep 2
echo "For my customized search open: http://localhost:8000/?title=data+%7C+computational+%7C+bioinformatician+%7C+bioinformatics+%7C+informatics+%7C+stat+%7C+statistics+%7C+statistical+%7C+programming+%7C+programmer+%7C+scientist+%7C+scientific+software+engineer+%7C+research+software+engineer+%7C+scientific+solutions+engineer+%7C+pipeline+developer+%7C+genomics+%7C+genomicist+%7C+genome+analyst+%7C+transcriptomics+analyst+%7C+proteomics+analyst+%7C+metabolomics+analyst+%7C+metagenomics+analyst+%7C+epigenomics+analyst+%7C+molecular+diagnostics+analyst+%7C+variant+analyst+%7C+clinical+variant+analyst+%7C+pharmacogenomics+analyst+%7C+precision+medicine+analyst+%7C+scientific+applications+analyst+%7C+pipeline+analyst+%7C+lims+analyst+%7C+scientific+operations+analyst"
sleep 2
python -m http.server 8000
bash
