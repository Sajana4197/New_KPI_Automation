# TX KPI Monitor

Reads the four daily IPPM KPI Excel files, stores the measurements for every site in PostgreSQL, and
produces the daily report of sites with packet drop, jitter or RTT above the threshold.

It runs on this computer only. Nothing is sent to the internet.

## Start and stop

1. Double-click **`run.bat`**. A window opens and the browser goes to <http://localhost:8000>.
2. The first time, you are asked to choose the password for the `admin` account.
3. To stop the system, press **Ctrl+C** in that window, or close it.

The system must be running for files to be imported. PostgreSQL starts with Windows on its own.

## Daily use

1. Copy the day's four KPI files into the **`inbox`** folder, or upload them on the **Imports** page.
   File names do not matter. Each file is recognised from its contents.
2. Wait until the dashboard shows the files as received. The four files take about half a minute.
3. Press **Generate report** on the dashboard.
4. Open the report, type feedback where needed, and press **Download Excel**.

If a file is missing you can still generate. The report then carries a warning that names the missing
file. When the file arrives later, import it and press **Regenerate**. Feedback you typed is kept.

## The rules of the report

| KPI | A site's hour counts when its worst path is at or above |
|---|---|
| Packet drop | 0.5 % |
| Jitter | 7 ms |
| RTT | 20 ms |

A site is listed for a KPI when 3 or more hours of the day count. Pending days are the previous
report's pending days plus one, or 0 if the site was not on the previous report. Feedback is carried
from the previous report. A site on its first day gets "TXOP to Check".

An administrator can change the thresholds and the default feedback under **Settings**. A change
applies to reports generated afterwards.

## Continuing from the old reports

Without starting history, every site begins at 0 pending days. To continue the old count:

1. Go to **Settings > Starting history**.
2. Enter the date of the last old report and choose its Excel file (the one with the Packet Drop,
   Jitter and RTT sheets).
3. Generate, or regenerate, the report for the day after that date.

## Accounts

| Role | Can do |
|---|---|
| Administrator | Everything, including users, thresholds and deleting data |
| Editor | Import files, generate reports, edit feedback |
| Viewer | View pages and download reports |

Administrators add users under **Settings > Users**. Everyone changes their own password under
**My account**.

## Folders

| Folder | Holds |
|---|---|
| `inbox` | Put new KPI files here. They are imported within a few seconds |
| `archive` | Imported files, in a folder for each report day |
| `failed` | Files that could not be read. The reason is on the Imports page |
| `reports` | A copy of each Excel report that was downloaded |

Data stays in the database until an administrator deletes a day on the **Imports** page. Deleting a
day does not delete its files from `archive`.

## Settings file

`.env` in this folder holds the database password, the port and the folder locations. `.env.example`
shows every setting. Keep `KPI_HOST=127.0.0.1` so that only this computer can open the system.

## Setting up on another computer

1. Install Python 3.13 and PostgreSQL 18.
2. In pgAdmin's Query Tool, run these two statements one at a time, with your own password:

   ```sql
   CREATE ROLE kpi_app LOGIN PASSWORD 'choose-a-password';
   ```
   ```sql
   CREATE DATABASE tx_kpi OWNER kpi_app;
   ```
3. Copy this folder without `.venv`, then in a terminal inside it:

   ```
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and fill in `KPI_DB_PASSWORD`.
5. Start with `run.bat`. The tables are created on the first start.

## If something goes wrong

| What you see | What to do |
|---|---|
| "The PostgreSQL database cannot be reached" | Start the `postgresql-x64-18` service in Windows Services, and check the password in `.env` |
| A file shows as **Failed** on the Imports page | Read the reason beside it. The file is in `failed`. Correct it and put it in `inbox` again |
| A file shows as **Already imported** | The same file was imported before. Nothing was changed |
| The report says thresholds have changed | Press **Regenerate** to apply the new thresholds to that day |
