import unittest

from mmapy import NeutrinoScanner
from ampel.log.AmpelLogger import AmpelLogger


logger = AmpelLogger()


class TestNeutrinoScanner(unittest.TestCase):

    maxDiff = None

    def test_scan(self):
        logger.info('\n\n Testing Neutrino Scanner \n\n')
        name = "IC200620A"
        expected_candidates = 2

        logger.info(f'scanning with neutrino {name}')
        nu = NeutrinoScanner(name, logger=logger)

        t_max = nu.default_t_max - 8

        nu.scan_cones(t_max=t_max)
        retrieved_candidates = len(nu.cache)

        logger.info(f"found {retrieved_candidates}, expected {expected_candidates}")
        self.assertEqual(expected_candidates, retrieved_candidates)

        nu.plot_overlap_with_observations(
            first_det_window_days=(t_max - nu.t_min).to("d").value
        )
        res = nu.draft_gcn()

        # Update the true using repr(res)
        true_gcn = "Astronomer Name (Institute of Somewhere), ............. report,\nOn behalf of the Zwicky Transient Facility (ZTF) and Global Relay of Observatories Watching Transients Happen (GROWTH) collaborations: \nWe observed the localization region of the neutrino event IceCube-200620A (Santander et. al, GCN 27997) with the Palomar 48-inch telescope, equipped with the 47 square degree ZTF camera (Bellm et al. 2019, Graham et al. 2019). We started observations in the g-band and r-band beginning at 2020-06-21T04:53:57.415 UTC, approximately 25.8 hours after event time. We covered 1.2 sq deg, corresponding to 77.7% of the reported localization region. This estimate accounts for chip gaps. Each exposure was 300s with a typical depth of 21.0 mag. \n \nThe images were processed in real-time through the ZTF reduction and image subtraction pipelines at IPAC to search for potential counterparts (Masci et al. 2019). AMPEL (Nordin et al. 2019, Stein et al. 2021) was used to search the alerts database for candidates. We reject stellar sources (Tachibana and Miller 2018) and moving objects, and apply machine learning algorithms (Mahabal et al. 2019) . We are left with the following high-significance transient candidates by our pipeline, all lying within the 90.0% localization of the skymap. \n\n+--------------------------------------------------------------------------------+\n| ZTF Name     | IAU Name  | RA (deg)    | DEC (deg)   | Filter | Mag   | MagErr |\n+--------------------------------------------------------------------------------+\n| ZTF18acvhwtf | AT2020ncs | 162.0678527 | +12.1263986 | r      | 20.11 | 0.16   | (MORE THAN ONE DAY SINCE SECOND DETECTION) \n| ZTF20abgvabi | AT2020ncr | 162.5306341 | +12.1461187 | g      | 20.58 | 0.19   | (MORE THAN ONE DAY SINCE SECOND DETECTION) \n+--------------------------------------------------------------------------------+\n\n \n\nAmongst our candidates, \nZTF18acvhwtf was first detected on 2458461.9815278. It has a spec-z of 0.291 [1548 Mpc] and an abs. mag of -20.8. Distance to SDSS galaxy is 0.52 arcsec. \nZTF20abgvabi was first detected on 2458995.6705903. \n. \n \nBased on observations obtained with the Samuel Oschin Telescope 48-inch and the 60-inch Telescope at the Palomar Observatory as part of the Zwicky Transient Facility project. ZTF is supported by the National Science Foundation under Grant No. AST-2034437 and a collaboration including Caltech, IPAC, the Weizmann Institute for Science, the Oskar Klein Center at Stockholm University, the University of Maryland, Deutsches Elektronen-Synchrotron and Humboldt University, the TANGO Consortium of Taiwan, the University of Wisconsin at Milwaukee, Trinity College Dublin, Lawrence Livermore National Laboratories, and IN2P3, France. Operations are conducted by COO, IPAC, and UW. \nGROWTH acknowledges generous support of the NSF under PIRE Grant No 1545949. \nAlert distribution service provided by DIRAC@UW (Patterson et al. 2019). \nAlert database searches are done by AMPEL (Nordin et al. 2019). \nAlert filtering is performed with the AMPEL Follow-up Pipeline (Stein et al. 2021). \n"

        self.assertEqual(
            res,
            true_gcn
        )
