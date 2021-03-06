#!/usr/bin/env python
# coding: utf-8

from ampel.ztf.archive.ArchiveDB import ArchiveDB
from astropy.time import Time
from astropy import units as u
from astropy.coordinates import SkyCoord
import numpy as np
import matplotlib.pyplot as plt
from ztfquery import alert, query
from matplotlib.backends.backend_pdf import PdfPages
import os
import getpass
import sqlalchemy
import healpy as hp
from tqdm import tqdm
from ampel.contrib.hu.t0.DecentFilter import DecentFilter
from ampel.pipeline.t0.DevAlertProcessor import DevAlertProcessor
from ampel.base.AmpelAlert import AmpelAlert
import datetime
import socket
import logging
from gwemopt.ztf_tiling import get_quadrant_ipix


ampel_user = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".AMPEL_user.txt")

try:
    with open(ampel_user, "r") as f:
        username = f.read()
except FileNotFoundError:
    username = getpass.getpass(prompt='Username: ', stream=None)
    with open(ampel_user, "wb") as f:
        f.write(username.encode())

ampel_pass = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".AMPEL_pass.txt")
        
try:
    with open(ampel_pass, "r") as f:
        password = f.read()
except FileNotFoundError:
    password = getpass.getpass(prompt='Password: ', stream=None)
    with open(ampel_pass, "wb") as f:
        f.write(password.encode())

if socket.gethostname() == "wgs33.zeuthen.desy.de":
    port = 5433
else:
    port = 5432

try:
    ampel_client = ArchiveDB('postgresql://{0}:{1}@localhost:{2}/ztfarchive'.format(username, password, port))
except sqlalchemy.exc.OperationalError as e:
    print("You can't access the archive database without first opening the port.")
    print("Open a new terminal, and into that terminal, run the following command:")
    print("ssh -L5432:localhost:5433 ztf-wgs.zeuthen.desy.de")
    print("If that command doesn't work, you are either not a desy user or you have a problem in your ssh config.")
    raise e


class MultiNightSummary(query._ZTFTableHandler_):

    def __init__(self, start_date=None, end_date=None):
        self.nights = self.find_nights(start_date, end_date)

        print("Using {0} Nightly Sumaries between {1} and {2}".format(
            len(self.nights), self.nights[0], self.nights[-1]))

        self.data, self.missing_nights = self.stack_nights()

        print("Of these, {0} nights are missing because ZTF did not observe.".format(len(self.missing_nights)))

    @staticmethod
    def find_nights(start_date=None, end_date=None):
        date_format = "%Y%m%d"

        if start_date is None:
            now = datetime.datetime.now()
            start_time = now - datetime.timedelta(days=30)
        else:
            start_time = datetime.datetime.strptime(start_date, date_format)  # .datetime()

        if end_date is None:
            end_time = datetime.datetime.now()
        else:
            end_time = datetime.datetime.strptime(end_date, date_format)

        if start_time > end_time:
            raise ValueError("Start time {0} occurs after end time {1}.".format(start_time, end_time))

        delta_t = (end_time - start_time).days

        dates = [(start_time + datetime.timedelta(days=x)).strftime(date_format) for x in range(0, delta_t + 1)]

        return dates

    def stack_nights(self):
        ns = None
        missing_nights = []

        for night in tqdm(self.nights):
            try:
                new_ns = self.get_ztf_data(night)

                # print(night)
                # print(ns is None)
                # print(hasattr(new_ns, "data"))
                # print(new_ns.data, new_ns.data is None)

                if ns is None:
                    if hasattr(new_ns, "data"):
                        if new_ns.data is not None:
                            ns = new_ns



                try:
                    ns.data = ns.data.append(new_ns.data)
                except AttributeError:
                    missing_nights.append(night)
            except ValueError:
                pass

        print(ns, missing_nights)

        if ns is None:
            raise Exception("No data found. The following were missing nights: \n {0}".format(missing_nights))

        return ns.data, missing_nights

    @staticmethod
    def get_ztf_data(date=None):
        """Function to grab data for a given date using ztfquery.
        Date should be given in format YYYYMMDD, with the day being the UT day for the END of the night.
        By default, today is selected. Returns a NightSummary if one is available, or None otherwise
        (None is returned if there are no ZTF observations).
        """
        if date is None:
            print("No date specified. Assuming today.")
            now = datetime.datetime.now()
            date = now.strftime("%Y%m%d")
        try:
            return query.NightSummary(date)
        # query returns an index error is no ztf data is found
        except IndexError:
            return None

class AmpelWizard:

    def __init__(self, run_config, t_min, logger=None, base_config=None, filter_class=DecentFilter, cone_nside=64,
                 fast_query=False, cones_to_scan=None):
        self.cone_nside = cone_nside
        self.t_min = t_min

        if not hasattr(self, "prob_threshold"):
            self.prob_threshold = None

        if base_config is None:
            base_config = {'catsHTM.default': "tcp://127.0.0.1:27020"}

        self.ampel_filter_class = filter_class(set(), base_config=base_config,
                                               run_config=filter_class.RunConfig(**run_config),
                                               logger=logger)
        self.dap = DevAlertProcessor(self.ampel_filter_class)

        if not hasattr(self, "output_path"):
            self.output_path = None

        self.scanned_pixels = []
        if cones_to_scan is None:
            self.cone_ids, self.cone_coords = self.find_cone_coords()
        else:
            self.cone_ids, self.cone_coords = cones_to_scan
        self.cache = dict()
        self.default_t_max = Time.now()

        self.mns_time = str(self.t_min).split("T")[0].replace("-", "")
        self.mns = None

        if fast_query:
            print("Scanning in fast mode!")
            self.query_ampel = self.fast_query_ampel

        self.overlap_prob = None
        self.overlap_fields = None
        self.first_obs = None
        self.last_obs = None
        self.n_fields = None
        self.area = None

    def get_name(self):
        raise NotImplementedError

    def get_full_name(self):
        raise NotImplementedError

    @staticmethod
    def get_tiling_line():
        return ""

    @staticmethod
    def get_obs_line():
        raise NotImplementedError

    @staticmethod
    def remove_variability_line():
        raise NotImplementedError

    def get_overlap_line(self):
        raise NotImplementedError

    def filter_ampel(self, res):
        return self.ampel_filter_class.apply(AmpelAlert(res['objectId'], *self.dap._shape(res))) is not None

    def get_avro_by_name(self, ztf_name):
        ztf_object = ampel_client.get_alerts_for_object(ztf_name, with_history=True)
        query_res = [i for i in ztf_object]
        query_res = self.merge_alerts(query_res)
        return query_res[0]

    def add_to_cache_by_names(self, *args):
        for ztf_name in args:
            self.cache[ztf_name] = self.get_avro_by_name(ztf_name)

    def check_ampel_filter(self, ztf_name):
        lvl = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.DEBUG)
        logging.info("Set logger level to DEBUG")
        query_res = self.get_avro_by_name(ztf_name)
        bool_ampel = self.filter_ampel(query_res)
        logging.info("Setting logger back to {0}".format(lvl))
        logging.getLogger().setLevel(lvl)
        return bool_ampel

    def plot_ztf_observations(self, **kwargs):
        self.get_multi_night_summary().show_gri_fields(**kwargs)

    def get_multi_night_summary(self, max_days=None):

        if max_days is not None:
            date_1 = datetime.datetime.strptime(self.mns_time, "%Y%m%d")
            end_date = date_1 + datetime.timedelta(days=max_days)
            end_date = end_date.strftime("%Y%m%d")
        else:
            end_date = None

        if self.mns is None:
            self.mns = MultiNightSummary(start_date=self.mns_time, end_date=end_date)
            times = np.array([Time(self.mns.data["UT_START"].iat[i], format="isot", scale="utc")
                     for i in range(len(self.mns.data))])
            mask = times > self.t_min
            self.mns.data = self.mns.data[mask]
        return self.mns

    # def simulate_observations(self, fields):

    def scan_cones(self, t_max=None, max_cones=None):

        if max_cones is None:
            max_cones = len(self.cone_ids)

        scan_radius = np.degrees(hp.max_pixrad(self.cone_nside))
        print("Commencing Ampel queries!")
        print("Scan radius is", scan_radius)
        print("So far, {0} pixels out of {1} have already been scanned.".format(
            len(self.scanned_pixels), len(self.cone_ids)
        ))

        for i, cone_id in enumerate(tqdm(list(self.cone_ids)[:max_cones])):
            ra, dec = self.cone_coords[i]

            if cone_id not in self.scanned_pixels:
                res = self.query_ampel(np.degrees(ra), np.degrees(dec), scan_radius, t_max)
                #                 print(len(res))
                for res_alert in res:

                    if res_alert['objectId'] not in self.cache.keys():
                        self.cache[res_alert['objectId']] = res_alert
                    elif res_alert['candidate']["jd"] > self.cache[res_alert['objectId']]['candidate']["jd"]:
                        self.cache[res_alert['objectId']] = res_alert
                self.scanned_pixels.append(cone_id)

        print("Scanned {0} pixels".format(len(self.scanned_pixels)))
        print("Found {0} candidates".format(len(self.cache)))

        self.create_candidate_summary()

    def filter_f_no_prv(self, res):
        raise NotImplementedError

    def fast_filter_f_no_prv(self, res):
        return self.filter_f_no_prv(res)

    def filter_f_history(self, res):
        raise NotImplementedError

    def fast_filter_f_history(self, res):
        return self.filter_f_history(res)

    def find_cone_coords(self):
        raise NotImplementedError

    @staticmethod
    def wrap_around_180(ra):
        ra[ra > np.pi] -= 2 * np.pi
        return ra

    def query_ampel(self, ra, dec, rad, t_max=None):

        if t_max is None:
            t_max = self.default_t_max

        ztf_object = ampel_client.get_alerts_in_cone(
            ra, dec, rad, self.t_min.jd, t_max.jd, with_history=False)
        query_res = [i for i in ztf_object]

        objectids = []
        for res in query_res:
            if self.filter_f_no_prv(res):
                if self.filter_ampel(res):
                    objectids.append(res["objectId"])

        ztf_object = ampel_client.get_alerts_for_object(objectids, with_history=True)

        query_res = [i for i in ztf_object]

        query_res = self.merge_alerts(query_res)

        final_res = []

        for res in query_res:
            if self.filter_f_history(res):
                final_res.append(res)

        return final_res

    def fast_query_ampel(self, ra, dec, rad, t_max=None):

        if t_max is None:
            t_max = self.default_t_max

        ztf_object = ampel_client.get_alerts_in_cone(
            ra, dec, rad, self.t_min.jd, t_max.jd, with_history=False)
        query_res = [i for i in ztf_object]

        indexes = []
        for i, res in enumerate(query_res):
            if self.fast_filter_f_no_prv(res):
                if self.filter_ampel(res):
                    indexes.append(i)

        final_res = [query_res[i] for i in indexes]
        # final_res = []
        #
        # for res in query_res:
        #     if self.fast_filter_f_history(res):
        #         final_res.append(res)

        return final_res

    @staticmethod
    def reassemble_alert(mock_alert):
        cutouts = ampel_client.get_cutout(mock_alert["candid"])
        for k in cutouts:
            mock_alert['cutout{}'.format(k.title())] = {'stampData': cutouts[k], 'fileName': 'dunno'}
        mock_alert['schemavsn'] = 'dunno'
        mock_alert['publisher'] = 'dunno'
        for pp in [mock_alert['candidate']] + mock_alert['prv_candidates']:
            # if pp['isdiffpos'] is not None:
            # pp['isdiffpos'] = ['f', 't'][pp['isdiffpos']]
            pp['pdiffimfilename'] = 'dunno'
            pp['programpi'] = 'dunno'
            pp['ssnamenr'] = 'dunno'

        return mock_alert

    @staticmethod
    def merge_alerts(alert_list):
        merged_list = []
        keys = list(set([x["objectId"] for x in alert_list]))

        for objectid in keys:
            alerts = [x for x in alert_list if x["objectId"] == objectid]
            if len(alerts) == 1:
                merged_list.append(alerts[0])
            else:
                jds = [x["candidate"]["jd"] for x in alerts]
                print(jds)
                order = [jds.index(x) for x in sorted(jds)[::-1]]
                latest = alerts[jds.index(max(jds))]
                latest["candidate"]["jdstarthist"] = min([x["candidate"]["jdstarthist"] for x in alerts])

                for index in order[1:]:

                    x = alerts[index]

                    # Merge previous detections

                    for prv in x["prv_candidates"] + [x["candidate"]]:
                        if prv not in latest["prv_candidates"]:
                            latest["prv_candidates"] = [prv] + latest["prv_candidates"]

                merged_list.append(latest)
        return merged_list

    def parse_candidates(self):

        table = "+------------------------------------------------------------------------------+\n" \
                "| ZTF Name     | IAU Name  | RA (deg)   | DEC (deg)  | Filter | Mag   | MagErr |\n" \
                "+------------------------------------------------------------------------------+\n"
        for name, res in sorted(self.cache.items()):

            jds = [x["jd"] for x in res["prv_candidates"]]

            if res["candidate"]["jd"] > max(jds):
                latest = res["candidate"]
            else:
                latest = res["prv_candidates"][jds.index(max(jds))]

            old_flag = ""

            second_det = [x for x in jds if x > min(jds) + 0.01]
            if len(second_det) > 0:
                if Time.now().jd - second_det[0] > 1.:
                    old_flag = "(MORE THAN ONE DAY SINCE SECOND DETECTION)"


            line = "| {0} | AT20FIXME | {1}{2}| {3}{4}{5}| {6}      | {7:.2f} | {8:.2f}   | {9}\n".format(
                name,
                latest["ra"],
                str(" ") * (11 - len(str(latest["ra"]))),
                ["", "+"][int(latest["dec"] > 0.)],
                latest["dec"],
                str(" ") * (11 - len(str(latest["dec"]))),
                ["g", "r", "i"][latest["fid"] - 1],
                latest["magpsf"],
                latest["sigmapsf"],
                old_flag
            )
            table += line

        table += "+------------------------------------------------------------------------------+\n\n"

        return table


    def draft_gcn(self):
        # candidate_text = parse_candidates(g)
        # first_obs =
        text = "Astronomer Name (Institute of Somewhere), ............. report,\n" \
               "On behalf of the Zwicky Transient Facility (ZTF) and Global Relay of Observatories Watching Transients Happen (GROWTH) collaborations: \n " \
               "We observed the localization region of the {0} with the Palomar 48-inch telescope, equipped with the 47 square degree ZTF camera (Bellm et al. 2019, Graham et al. 2019). {1}" \
               "We started observations in the g-band and r-band beginning at {2} UTC, " \
               "approximately {3:.1f} hours after event time. {4}" \
               "{5} \n \n" \
               "The images were processed in real-time through the ZTF reduction and image subtraction pipelines at IPAC to search for potential counterparts (Masci et al. 2019). " \
               "AMPEL (Nordin et al. 2019) was used to search the alerts database for candidates. " \
               "We reject stellar sources (Tachibana and Miller 2018) and moving objects, and" \
               "apply machine learning algorithms (Mahabal et al. 2019) {6}. We are left with the following high-significance transient " \
               "candidates by our pipeline, all lying within the " \
               "{7}% localization of the skymap. \n\n{8} \n\n" \
               "Amongst our candidates, {9}. \n \n".format(
            self.get_full_name(),
            self.get_tiling_line(),
            self.first_obs.utc,
            (self.first_obs.jd - self.t_min.jd) * 24.,
            self.get_overlap_line(),
            self.get_obs_line(),
            self.remove_variability_line(),
            100*self.prob_threshold,
            self.parse_candidates(),
            self.text_summary()
        )

        text += "ZTF and GROWTH are worldwide collaborations comprising Caltech, USA; IPAC, USA, WIS, Israel; OKC, Sweden; JSI/UMd, USA; U Washington, USA; DESY, Germany; MOST, Taiwan; UW Milwaukee, USA; LANL USA; Tokyo Tech, Japan; IITB, India; IIA, India; LJMU, UK; TTU, USA; SDSU, USA and USyd, Australia. \n"
        "ZTF acknowledges the generous support of the NSF under AST MSIP Grant No 1440341. \n"
        "GROWTH acknowledges generous support of the NSF under PIRE Grant No 1545949. \n "
        "Alert distribution service provided by DIRAC@UW (Patterson et al. 2019). \n"
        "Alert database searches are done by AMPEL (Nordin et al. 2019). \n"
        "Alert filtering and follow-up coordination is being undertaken by the GROWTH marshal system (Kasliwal et al. 2019)."
        return text

    @staticmethod
    def extract_ra_dec(nside, index):
        (colat, ra) = hp.pix2ang(nside, index, nest=True)
        dec = np.pi / 2. - colat
        return (ra, dec)

    @staticmethod
    def extract_npix(nside, ra, dec):
        colat = np.pi / 2. - dec
        return hp.ang2pix(nside, colat, ra, nest=True)

    def create_candidate_summary(self):

        print("Saving to:", self.output_path)

        with PdfPages(self.output_path) as pdf:
            for (name, old_alert) in tqdm(sorted(self.cache.items())):
                mock_alert = self.reassemble_alert(old_alert)
                fig = alert.display_alert(mock_alert, show_ps_stamp=True)
                fig.text(0, 0, name)
                pdf.savefig()
                plt.close()

    @staticmethod
    def parse_ztf_filter(fid):
        return ["g", "r", "i"][fid - 1]

    def tns_summary(self):
        for name, res in sorted(self.cache.items()):
            detections = [x for x in res["prv_candidates"] + [res["candidate"]] if x["isdiffpos"] is not None]
            detection_jds = [x["jd"] for x in detections]
            first_detection = detections[detection_jds.index(min(detection_jds))]
            latest = [x for x in res["prv_candidates"] + [res["candidate"]] if x["isdiffpos"] is not None][-1]
            last_upper_limit = [x for x in res["prv_candidates"] if
                                np.logical_and(x["isdiffpos"] is None, x["jd"] < first_detection["jd"])][-1]
            print("Candidate:", name, res["candidate"]["ra"], res["candidate"]["dec"], first_detection["jd"])
            print("Last Upper Limit:", last_upper_limit["jd"], self.parse_ztf_filter(last_upper_limit["fid"]),
                  last_upper_limit["diffmaglim"])
            print("First Detection:", first_detection["jd"], self.parse_ztf_filter(first_detection["fid"]),
                  first_detection["magpsf"], first_detection["sigmapsf"])
            print("First observed {0} hours after merger".format(24. * (first_detection["jd"] - self.t_min.jd)))
            print("It has risen", -latest["magpsf"] + last_upper_limit["diffmaglim"],
                  self.parse_ztf_filter(latest["fid"]), self.parse_ztf_filter(last_upper_limit["fid"]))
            print([x["jd"] for x in res["prv_candidates"] + [res["candidate"]] if x["isdiffpos"] is not None])
            print("\n")


    def candidate_text(self, name, first_detection, lul_lim, lul_jd):
        raise NotImplementedError

    def text_summary(self):
        text = ""
        for name, res in sorted(self.cache.items()):
            detections = [x for x in res["prv_candidates"] + [res["candidate"]] if x["isdiffpos"] is not None]
            detection_jds = [x["jd"] for x in detections]
            first_detection = detections[detection_jds.index(min(detection_jds))]
            latest = [x for x in res["prv_candidates"] + [res["candidate"]] if x["isdiffpos"] is not None][-1]
            last_upper_limit = [x for x in res["prv_candidates"] if
                                np.logical_and(x["isdiffpos"] is None, x["jd"] < first_detection["jd"])][-1]

            text += self.candidate_text(name, first_detection["jd"], last_upper_limit["diffmaglim"],
                                        last_upper_limit["jd"])
            # print("Candidate:", name, res["candidate"]["ra"], res["candidate"]["dec"], first_detection["jd"])
            # print("Last Upper Limit:", last_upper_limit["jd"], self.parse_ztf_filter(last_upper_limit["fid"]),
            #       last_upper_limit["diffmaglim"])
            # print("First Detection:", first_detection["jd"], self.parse_ztf_filter(first_detection["fid"]),
            #       first_detection["magpsf"], first_detection["sigmapsf"])
            # print("First observed {0} hours after merger".format(24. * (first_detection["jd"] - g.t_min.jd)))
            # print("It has risen", -latest["magpsf"] + last_upper_limit["diffmaglim"],
            #       self.parse_ztf_filter(latest["fid"]), self.parse_ztf_filter(last_upper_limit["fid"]))
            # print([x["jd"] for x in res["prv_candidates"] + [res["candidate"]] if x["isdiffpos"] is not None])
            # print("\n")

            c = SkyCoord(res["candidate"]["ra"], res["candidate"]["dec"], unit="deg")
            g_lat = c.galactic.b.degree
            if abs(g_lat) < 15.:
                text += "It is located at a galactic latitude of {0:.2f} degrees. ".format(
                    g_lat
                )
            text += "\n "
        return text

    def simple_plot_overlap_with_observations(self, fields=None, first_det_window_days=None):

        nside = hp.pixelfunc.nside2npix(len(self.map_coords))

        fig = plt.figure()
        plt.subplot(projection="aitoff")

        probs = []
        single_probs = []

        if fields is None:
            mns = self.get_multi_night_summary(first_det_window_days)

        else:

            class MNS:
                def __init__(self, data):
                    self.data = pandas.DataFrame(data, columns=["field", "ra", "dec", "UT_START"])

            data = []

            for f in fields:
                ra, dec = ztfquery_fields.field_to_coords(f)[0]
                t = Time(Time.now().jd + 1., format="jd").utc
                t.format = "isot"
                t = t.value
                for _ in range(2):
                    data.append([f, ra, dec, t])

            mns = MNS(data)

        ras = np.degrees(self.wrap_around_180(np.array([
            np.radians(float(x)) for x in mns.data["ra"]])))

        fields = list(mns.data["field"])

        plot_ras = []
        plot_decs = []

        single_ras = []
        single_decs = []

        veto_ras = []
        veto_decs = []

        overlapping_fields = []

        base_ztf_rad = 3.5
        ztf_dec_deg = 30.

        for j, (ra, dec) in enumerate(tqdm(self.map_coords)):
            ra_deg = np.degrees(self.wrap_around_180(np.array([ra])))
            # ra_deg = self.wrap_around_180(np.array(np.degrees(ra)))
            dec_deg = np.degrees(dec)
            # (np.cos(dec - np.radians(ztf_dec_deg))
            ztf_rad = base_ztf_rad
            ztf_height = 3.7

            n_obs = 0

            for i, x in enumerate(mns.data["dec"]):
                if np.logical_and(not dec_deg < float(x) - ztf_height, not dec_deg > float(x) + ztf_height):
                    if abs(dec_deg - ztf_dec_deg) < 70.:
                        if np.logical_and(not ra_deg < float(ras[i]) - ztf_rad/ abs(np.cos(dec)),
                                          not ra_deg > float(ras[i]) + ztf_rad/ abs(np.cos(dec))):
                            n_obs += 1
                            fid = fields[i]
                            if fid not in overlapping_fields:
                                overlapping_fields.append(fields[i])

            if n_obs > 1:
                probs.append(self.map_probs[j])
                plot_ras.append(ra)
                plot_decs.append(dec)

            elif n_obs > 0:
                single_probs.append(self.map_probs[j])
                single_ras.append(ra)
                single_decs.append(dec)

            else:
                veto_ras.append(ra)
                veto_decs.append(dec)

        overlapping_fields = list(set(overlapping_fields))

        obs_times = np.array([Time(mns.data["UT_START"].iat[i], format="isot", scale="utc")
                     for i in range(len(mns.data)) if mns.data["field"].iat[i] in overlapping_fields])

        self.first_obs = min(obs_times)
        self.last_obs = max(obs_times)

        size = hp.max_pixrad(nside, degrees=True)**2

        # print(hp.max_pixrad(self.ligo_nside, degrees=True)**2 * np.pi, size)

        plt.scatter(self.wrap_around_180(np.array([plot_ras])), plot_decs,
                    c=probs, vmin=0., vmax=max(self.data[self.key]), s=size)

        plt.scatter(self.wrap_around_180(np.array([single_ras])), single_decs,
                    c=single_probs, vmin=0., vmax=max(self.data[self.key]), s=size, cmap='gray')

        plt.scatter(self.wrap_around_180(np.array([veto_ras])), veto_decs, color="red", s=size)

        red_patch = mpatches.Patch(color='red', label='Not observed')
        gray_patch = mpatches.Patch(color='gray', label='Observed once')
        plt.legend(handles=[red_patch, gray_patch])

        self.overlap_prob = 100.*np.sum(probs)

        message = "In total, {0} % of the contour was observed at least once. \n " \
                  "In total, {1} % of the contour was observed at least twice. \n" \
                  "THIS DOES NOT INCLUDE CHIP GAPS!!!".format(
            100 * (np.sum(probs) + np.sum(single_probs)), self.overlap_prob)

        print(message)

        self.area = (2. * base_ztf_rad)**2 * float(len(overlapping_fields))
        self.n_fields = len(overlapping_fields)
        self.overlap_fields = overlapping_fields

        print("{0} fields were covered, covering approximately {1} sq deg.".format(
            self.n_fields, self.area))
        return fig, message

    def plot_overlap_with_observations(self, fields=None, pid=None, first_det_window_days=None):

        try:
            nside = self.ligo_nside
        except AttributeError:
            nside = self.nside

        fig = plt.figure()
        plt.subplot(projection="aitoff")

        probs = []
        single_probs = []

        if fields is None:
            mns = self.get_multi_night_summary(first_det_window_days)

        else:

            class MNS:
                def __init__(self, data):
                    self.data = pandas.DataFrame(data, columns=["field", "ra", "dec", "UT_START"])

            data = []

            for f in fields:
                ra, dec = ztfquery_fields.field_to_coords(f)[0]
                for i in range(2):
                    t = Time(self.t_min.jd + 0.1*i, format="jd").utc
                    t.format = "isot"
                    t = t.value
                    data.append([f, ra, dec, t])

            mns = MNS(data)

        data = mns.data.copy()

        if pid is not None:
            pid_mask = data["pid"] == str(pid)
            data = data[pid_mask]

        obs_times = np.array([Time(data["UT_START"].iat[i], format="isot", scale="utc")
                              for i in range(len(data))])

        if first_det_window_days is not None:
            first_det_mask = [x < Time(self.t_min.jd + first_det_window_days, format="jd").utc for x in obs_times]
            data = data[first_det_mask]
            obs_times = obs_times[first_det_mask]

        pix_obs_times = dict()

        print("Unpacking observations")

        overlapping_fields = []

        for i, obs_time in enumerate(tqdm(obs_times)):
            pix = get_quadrant_ipix(nside, data["ra"].iat[i], data["dec"].iat[i])

            flat_pix = []

            for sub_list in pix:
                for p in sub_list:
                    flat_pix.append(p)

            flat_pix = list(set(flat_pix))

            t = obs_time.jd
            for p in flat_pix:
                if p not in pix_obs_times.keys():
                    pix_obs_times[p] = [t]
                else:
                    pix_obs_times[p] += [t]

        self.overlap_fields = overlapping_fields

        plot_pixels = []
        probs = []
        single_pixels = []
        single_probs = []
        veto_pixels = []
        times = []

        print("Checking skymap")

        for i, p in enumerate(tqdm(hp.nest2ring(nside, self.pixel_nos))):

            if p in pix_obs_times.keys():

                obs = pix_obs_times[p]

                if max(obs) - min(obs) > 0.01:
                    plot_pixels.append(p)
                    probs.append(self.map_probs[i])
                else:
                    single_pixels.append(p)
                    single_probs.append(self.map_probs[i])

                times += obs
            else:
                veto_pixels.append(p)

        self.overlap_prob = np.sum(probs + single_probs) * 100.

        print(plot_pixels, probs)

        size = hp.max_pixrad(nside) ** 2 * 50.

        veto_pos = np.array([hp.pixelfunc.pix2ang(nside, i, lonlat=True) for i in veto_pixels]).T

        plt.scatter(self.wrap_around_180(np.radians(veto_pos[0])), np.radians(veto_pos[1]),
                    color="red", s=size)

        single_pos = np.array([hp.pixelfunc.pix2ang(nside, i, lonlat=True) for i in single_pixels]).T

        if len(single_pos) > 0:

            plt.scatter(self.wrap_around_180(np.radians(single_pos[0])), np.radians(single_pos[1]),
                        c=single_probs, vmin=0., vmax=max(self.data[self.key]), s=size, cmap='gray')

        plot_pos = np.array([hp.pixelfunc.pix2ang(nside, i, lonlat=True) for i in plot_pixels]).T

        if len(plot_pos) > 0:
            plt.scatter(self.wrap_around_180(np.radians(plot_pos[0])), np.radians(plot_pos[1]),
                        c=probs, vmin=0., vmax=max(self.data[self.key]), s=size)

        red_patch = mpatches.Patch(color='red', label='Not observed')
        gray_patch = mpatches.Patch(color='gray', label='Observed once')
        plt.legend(handles=[red_patch, gray_patch])

        message = "In total, {0} % of the LIGO contour was observed at least once. \n " \
                  "In total, {1} % of the LIGO contour was observed at least twice. \n" \
                  "This estimate accounts for chip gaps.".format(
            100 * (np.sum(probs) + np.sum(single_probs)), 100.*np.sum(probs))

        all_pix = single_pixels + plot_pixels

        n_pixels = len(single_pixels + plot_pixels)

        self.area = hp.pixelfunc.nside2pixarea(nside, degrees=True) * n_pixels

        self.first_obs = Time(min(times), format="jd")
        self.last_obs = Time(max(times), format="jd")

        print("Observations started at {0}".format(self.first_obs.jd))

        self.overlap_fields = overlapping_fields

        #     area = (2. * base_ztf_rad)**2 * float(len(overlapping_fields))
        #     n_fields = len(overlapping_fields)

        print("{0} pixels were covered, covering approximately {1} sq deg.".format(
            n_pixels, self.area))
        return fig, message
