import pickle
import argparse
from multiprocessing import JoinableQueue, Process
from gw_scanner import GravWaveScanner
import random
import numpy as np
import healpy as hp
import os
from pathlib import Path
from ampel_magic import ampel_client
from tqdm import tqdm
import pickle as pickle


ligo_candidate_cache = os.path.join(Path().absolute(), "LIGO_cache")

class MultiGwProcessor(GravWaveScanner):
    queue = None
    results = dict()

    def __init__(self, n_cpu, id=0, **kwargs):
        GravWaveScanner.__init__(self, **kwargs)
        self.cache_dir = os.path.join(
            ligo_candidate_cache,
            os.path.splitext(os.path.basename(self.output_path))[0]
        )
        try:
            os.makedirs(self.cache_dir)
        except OSError:
            pass
        self.scan_radius = np.degrees(hp.max_pixrad(self.cone_nside))
        self.queue = JoinableQueue()
        self.processes = [Process(target=self.scan_wrapper, kwargs={"id": i+1}) for i in range(int(n_cpu))]

        self.obj_names = []
        self.mp_id = id

        for p in self.processes:
            p.start()

    def add_to_queue(self, item):
        self.queue.put(item)

    def scan_wrapper(self, **kwargs):
        self.mp_id = kwargs["id"]

        while True:
            item = self.queue.get()
            if item is None:
                break

            (j, mts, query_res) = item

            print("{0} of {1} queries: Staring {2} alerts".format(j, mts, len(query_res)))

            res = self.filter(query_res)

            print("{0} of {1} queries: {2} accepted out of {3} alerts".format(j, mts, len(res), len(query_res)))

            self.obj_names += [x["objectId"] for x in res]
            if len(res) > 0:
                self.dump_cache()

            # self.dump_cache(res)
            self.queue.task_done()

    def filter(self, query_res):

        indexes = []

        for i, res in enumerate(query_res):
            if self.filter_f_no_prv(res):
                # if self.filter_ampel(res) is not None:
                indexes.append(i)

        return [query_res[i] for i in indexes]

    def filter_f_no_prv(self, res):

        # Positive detection
        if res['candidate']['isdiffpos'] not in ["t", "1"]:
            return False

        # Veto old transients
        if res["candidate"]["jdstarthist"] < self.t_min.jd:
            return False

        # Check contour
        if not self.in_contour(res["candidate"]["ra"], res["candidate"]["dec"]):
            return False

        # Require 2 detections separated by 15 mins
        if (res["candidate"]["jdendhist"] - res["candidate"]["jdstarthist"]) < 0.01:
            return False

        return True

    # def dump_cache(self, res):
    #     for obj in res:
    #         path = os.path.join(self.cache_dir, obj["objectId"] + ".pkl")
    #         with open(path, "wb") as f:
    #             pickle.dump(obj, f)
    def dump_cache(self):

        path = os.path.join(self.cache_dir, "{0}.pkl".format(self.mp_id))

        with open(path, "wb") as f:
            pickle.dump(self.obj_names, f)

    def fill_queue(self):

        t_max = self.default_t_max

        time_steps = np.arange(self.t_min.jd, t_max.jd, step=0.005)
        mts = len(time_steps)

        n_tot = 0

        print("Scanning between {0}JD and {1}JD".format(time_steps[0], time_steps[-1]))

        for j, t_start in enumerate(tqdm(list(time_steps[:-1]))):

            ztf_object = ampel_client.get_alerts_in_time_range(
                jd_min=t_start, jd_max=time_steps[j+1], with_history=False)
            query_res = [x for x in ztf_object]
            n_tot += len(query_res)
            r.add_to_queue((j, mts, query_res))
            self.scanned_pixels.append(j)

        print("Added {0} candidates since {1}".format(n_tot, time_steps[0]))

    def terminate(self):
        """ wait until queue is empty and terminate processes """
        self.queue.join()
        for p in self.processes:
            p.terminate()

    def combine_cache(self):

        for name in self.get_cache_file():
            try:
                with open(os.path.join(self.cache_dir, name), "rb") as f:
                    self.obj_names += pickle.load(f)
            except:
                pass

        self.obj_names = list(set(self.obj_names))

        print("Scanned {0} pixels".format(len(self.scanned_pixels)))
        print("Found {0} candidates".format(len(self.obj_names)))

        ztf_object = ampel_client.get_alerts_for_object(self.obj_names, with_history=True)

        query_res = [i for i in ztf_object]

        query_res = self.merge_alerts(query_res)

        self.cache = dict()

        for res in tqdm(query_res):
            if self.filter_f_history(res):
                if self.filter_ampel(res) is not None:
                    self.cache[res["objectId"]] = res

        self.create_candidate_summary()

    def get_cache_file(self):
        return [os.path.join(self.cache_dir, x) for x in os.listdir(self.cache_dir) if ".pkl" in x]

    def clean_cache(self):

        for name in self.get_cache_file():
            os.remove(name)

        print("Cache cleaned!")

if __name__ == '__main__':
    import os
    import logging

    logger = logging.getLogger("quiet_logger")
    logger.setLevel(logging.ERROR)

    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--n_cpu", default=min(24, max(1, os.cpu_count()-1)))
    parser.add_argument("-p", "--prob_threshold", default=0.9, type=float)
    cfg = parser.parse_args()

    print("N CPU available", os.cpu_count())
    print("Using {0} CPUs".format(cfg.n_cpu))

    r = MultiGwProcessor(n_cpu=cfg.n_cpu, logger=logger, prob_threshold=cfg.prob_threshold, fast_query=True)
    # r.clean_cache()
    r.fill_queue()
    r.terminate()
    r.combine_cache()
    # r.clean_cache()