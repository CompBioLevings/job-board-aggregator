# Job Board Aggregator  

Automated job board aggregating thousands of positions from hundreds to thousands of companies using Greenhouse, Workday and other ATS platforms.  

## Set-up  

I've included an environment file to create an Anaconda environment to install all dependencies for running these scripts.  So simply execute the following with `conda` (or sub in `mamba` if you prefer, as I do):  

```shell
$ conda env create -n jobagg -f scripts/conda_env.yml
```  

## Run it (local development)  

Simply run the `auto_run.sh` script as follows:  

```shell
$ source auto_run.sh
```  

Then open the URL output to the console to look at the job board locally and search through it for relevant openings.  

<br>

Here's a more detailed breakdown of what the `auto_run.sh` script is doing:  

```shell
$ cd job-board-aggregator
$ conda activate jobagg
$ python3 scripts/scraper.py --source manual
$ python3 scripts/merge_data.py
$ python -m http.server 8000
```  

<br>

## Features  

- Real-time filtering by title, company, location  
- Sortable columns  
- Pagination for large datasets  

## Tech stack  

- **Frontend:** Vanilla JavaScript, Bootstrap 5, HTML/CSS  
- **Scraping:** Python (requests, concurrent.futures)  
- **Data:** JSON  

<br>

## Attribution  

Original concept and scripts taken from GitHub of [Riley Dorrington](https://github.com/Feashliaa) and then I updated for biotech job search and added some improvements.  

