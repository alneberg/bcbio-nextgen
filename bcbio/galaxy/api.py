"""Access LIMS functionality via diffent APIs (Galaxy and Genologics LIMS).
"""
import urllib
import urllib2
import json
import time

class ApiAccess(object):
    """ Generic base class for REST API methods
    """
    def __init__(self, galaxy_url, api_key):
        self._key = api_key
        self._max_tries = 5

    def _make_url(self, rel_url, params=None):
        if not params:
            params = dict()
        params['key'] = self._key
        vals = urllib.urlencode(params)
        return ("%s%s" % (self._base_url, rel_url), vals)

    def _get(self, url, params=None):
        url, params = self._make_url(url, params)
        num_tries = 0
        while 1:
            response = urllib2.urlopen("%s?%s" % (url, params))
            try:
                out = json.loads(response.read())
                break
            except ValueError:
                if num_tries > self._max_tries:
                    raise
                time.sleep(3)
                num_tries += 1
        return out

    def _post(self, url, data, params=None, need_return=True):
        url, params = self._make_url(url, params)
        request = urllib2.Request("%s?%s" % (url, params),
                headers = {'Content-Type' : 'application/json'},
                data = json.dumps(data))
        response = urllib2.urlopen(request)
        try:
            data = json.loads(response.read())
        except ValueError:
            if need_return:
                raise
            else:
                data = {}
        return data

class GenologicsApiAccess(ApiAccess):
    """Access commercial Genologics LIMS REST API via genologics python module
    """
    def __init__(self):
        try:
            from genologics.lims import *
        except ImportError:
            raise ImportError("Genologics python module not installed, please run 'pip install genologics'")

        from genologics.config import BASEURI, USERNAME, PASSWORD
        lims = Lims(BASEURI, USERNAME, PASSWORD)
        lims.check_version()

    def run_details(self, run_bc, run_date=None):
        """Get run details from the LIMS API that the pipeline can use.
        """
        raise NotImplemented("#XXX: Transform the returned data from Brad's nglims")

    def get_projects(self):
        return lims.get_projects()


class GalaxyApiAccess(ApiAccess):
    """Simple front end for accessing Galaxy's REST API.
    """
    super(ApiAccess)

    def __init__(self, galaxy_url, api_key):
        self._base_url = galaxy_url

    def run_details(self, run_bc, run_date=None):
        """Next Gen LIMS specific API functionality.
        """
        try:
            details = self._get("/nglims/api_run_details", dict(run=run_bc))
        except ValueError:
            raise ValueError("Could not find information in Galaxy for run: %s" % run_bc)
        if "error" in details and run_date is not None:
            try:
                details = self._get("/nglims/api_run_details", dict(run=run_date))
            except ValueError:
                raise ValueError("Could not find information in Galaxy for run: %s" % run_date)
        return details

    def sequencing_projects(self):
        """Next Gen LIMS: retrieve summary information of sequencing projects.
        """
        return self._get("/nglims/api_projects")

    def sqn_run_summary(self, run_info):
        """Next Gen LIMS: Upload sequencing run summary information.
        """
        return self._post("/nglims/api_upload_sqn_run_summary", data=run_info)

    def sqn_report(self, start_date, end_date):
        """Next Gen LIMS: report of items sequenced in a time period.
        """
        return self._get("/nglims/api_sqn_report",
                dict(start=start_date, end=end_date))
