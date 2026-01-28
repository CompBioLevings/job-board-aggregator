# Job Board Aggregator  

Automated job board aggregating thousands of positions from hundreds to thousands of companies using Greenhouse and other ATS platforms.  

## Features  
- Real-time filtering by title, company, location  
- Sortable columns  
- Pagination for large datasets  

## Tech Stack  
- **Frontend:** Vanilla JavaScript, Bootstrap 5, HTML/CSS  
- **Scraping:** Python (requests, concurrent.futures)  
- **Data:** JSON  

## Local Development  
*Note:* Takes about 5 min to run end-to-end locally.  Use the following steps to update and view new jobs.  

```
cd job-board-aggregator
conda activate textscrape
python3 scripts/scraper.py --source manual
python3 scripts/merge_data.py
python -m http.server 8000
```
Then visit [http://localhost:8000](http://localhost:8000)  

## Attribution  
Original concept and scripts taken from GitHub of [Riley Dorrington](https://github.com/Feashliaa) and then I updated for biotech job search and added some improvements.  

