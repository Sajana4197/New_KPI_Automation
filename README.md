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

A site is one name in the KPI file. Each site has one or two path types, and each path type is checked
on its own:

| File | Path types |
|---|---|
| Huawei IPPM | **BE** (task name ends in `_dscp_0`) and **Priority** (`_dscp_46`). The site name is the task name without that ending. |
| ZTE (all three files) | **SLT** (destination IP 60.x.x.x to 69.x.x.x) and **Mobitel** (any other destination IP) |

The same ID under two different names counts as two sites.

| KPI | An hour counts when the worst path of that path type is at or above |
|---|---|
| Packet drop | 0.5 % |
| Jitter | 7 ms |
| RTT | 20 ms |

A path type is listed for a KPI when 3 or more hours of the day count. If both path types of a site
are listed, the report has a row for each. Pending days are the previous
report's pending days plus one, or 0 if the site was not on the previous report. The TXP remark (the
reason for the problem, or what is needed to fix it) starts as "TXOP to check" for a site on its first
day. Anyone signed in can edit it on the Report page: click the remark in the row, or tick several rows
and give them one remark. It is saved in the database, printed as "TXP Remark" in the Excel report, and
carried to the next day's report while the site stays listed.

An administrator can change the thresholds and the default TXP remark under **Settings**. A change
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
| Administrator | Everything: import files, generate reports, edit TXP remarks, manage users, change thresholds, delete data |
| User | View the dashboard, reports and sites, download the Excel report, and type TXP remarks. Cannot import files, generate reports or change settings |

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

## Fetching the dump files from a server

The system can fetch the KPI dump files from a Linux server by itself, over SFTP (the same login as
SSH). It only reads from the server; nothing there is changed or deleted.

1. Open `.env` and fill in the `KPI_SFTP_` settings. `.env.example` lists them:

   | Setting | Meaning |
   |---|---|
   | `KPI_SFTP_HOST`, `KPI_SFTP_PORT` | Server address and port (normally 22) |
   | `KPI_SFTP_USER` | User name on the server |
   | `KPI_SFTP_KEY_FILE` | Path to your private SSH key file (recommended), or |
   | `KPI_SFTP_PASSWORD` | the password, if you do not use a key |
   | `KPI_SFTP_DIR` | Folder on the server that holds the dump files |
   | `KPI_SFTP_MINUTES` | How often to check (default 5) |
   | `KPI_SFTP_DAYS` | How many days back a routine check looks (default 2) |

2. Close and start `run.bat` again.
3. Open **Imports**. The **Server** card shows whether the connection works and what was fetched.

A routine check takes only Excel files written in the last `KPI_SFTP_DAYS` days that it has not
fetched before. To load older days, open **Fetch older days from the server** on the Server card and
choose the days (up to 31 at a time).

The first connection remembers the server's identity in `instance\known_hosts`. If the server is
rebuilt and the system then refuses to connect, delete that file and check again.

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
