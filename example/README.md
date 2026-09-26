# Example project

A one-page Django site with oss-clarity installed, to see the whole loop on
your own machine: tracking, recording, analysis and heatmaps.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e "..[django]"        # from this folder
python manage.py migrate
python manage.py createsuperuser
python manage.py example_site      # creates the site, recording on
python manage.py runserver
```

1. Open <http://localhost:8000/> in a normal browser window. Click the panel
   that does nothing a few times fast, click "Book a service", scroll, and
   go to Pricing and back.
2. Close the tab and wait a minute (this project ends a visit after one quiet
   minute; the default is 30).
3. Run the jobs: `python manage.py oss_clarity_run_jobs`
4. Open <http://localhost:8000/admin/>: the recording, with its rage and dead
   clicks marked, is under Recordings; the site's page has a Heatmap link.

Browsers driven by automation (`navigator.webdriver`, headless Chrome) are
treated as crawlers and never recorded, so use an ordinary browser window.
Global Privacy Control turns recording off too.
