# AI Jobs in Pakistan

Weekly tracker of AI and data roles in Pakistan, built by Densight Labs.

**Daily (7 AM PKT):** scrape Indeed and LinkedIn, keep only AI/data roles, remove duplicates
and reposts, save new jobs to `data/jobs.csv` and the master Sheet (`all_jobs` tab).

**Weekly (Monday 9 AM PKT):** for last Monday to Sunday:
- new tab in the **public** Sheet with that week's jobs (signup banner in row 1)
- `companies` tab in the **master** Sheet: lead list with a Cold / Warm / Hot / Very hot signal
- `weekly_stats` tab in the master Sheet: one row per week for trends
- 6 square chart images for the carousel, plus `summary.md` with the numbers and top 10 jobs for the email

## Setup

1. **Apps Script:** replace your code with `apps_script/Code.gs`, fill in both Sheet IDs, then
   Deploy > Manage deployments > edit > Version: *New version* > Deploy. The URL stays the same.
2. **Push this folder** to your `ai-jobs-pakistan` repo (secrets `APPS_SCRIPT_URL` and
   `APPS_SCRIPT_SECRET` should already be there).
3. **Settings > Actions > General > Workflow permissions:** choose *Read and write permissions*
   (lets the workflow save job history back to the repo).
4. **Edit `config.yaml`:** put your real signup form link in `signup_url`.
5. **Test:** Actions tab > *Daily scrape* > Run workflow. Check the master Sheet for new rows.
6. After the daily job has run for a few days: Actions > *Weekly report* > Run workflow.
   Open the run to see the summary; download charts from *Artifacts* at the bottom.

## Every Monday

1. Open the latest *Weekly report* run: read the summary, download the charts.
2. Skim the new public tab for anything misclassified (fix rules in `config.yaml`).
3. Send the email (top 10 picks + link to the public Sheet), post the carousel.
4. Check the `companies` tab for Hot / Very hot companies to contact.

## Tuning

Everything lives in `config.yaml`: search terms, results per site, category rules,
excluded titles, cities, seniority words and the skills list. No code changes needed.

## Testing locally without touching the Sheets

```bash
pip install -r requirements.txt
DRY_RUN=1 python -m pipeline.scrape     # writes Sheet payloads to ./out instead
DRY_RUN=1 WEEK=2026-W41 python -m pipeline.weekly
```
