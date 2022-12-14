import logging
from itertools import combinations
import numpy as np

import scipy.spatial as spatial

from opensfm import bow


logger = logging.getLogger(__name__)


def has_gps_info(exif):
    return (exif and
            'gps' in exif and
            'latitude' in exif['gps'] and
            'longitude' in exif['gps'])


def bow_distances(image, other_images, words, masks, bows):
    """ Compute BoW-based distance (L1 on histogram of words)
        between an image and other images.

        Can use optionaly masks for discarding some features
    """
    # params
    min_num_feature = 8

    if words[image] is None:
        logger.error("Could not load words for image {}".format(image))
        return []

    filtered_words = words[image][masks[image]] if masks else words[image]
    if len(filtered_words) <= min_num_feature:
        logger.warning("Too few filtered features in image {}: {}".format(
            image, len(filtered_words)))
        return []

    # Compute distance
    distances = []
    other = []
    h = bows.histogram(filtered_words[:, 0])
    for im2 in other_images:
        if im2 != image and words[im2] is not None:
            im2_words = words[im2][masks[im2]] if masks else words[im2]
            if len(im2_words) > min_num_feature:
                h2 = bows.histogram(im2_words[:, 0])
                distances.append(np.fabs(h - h2).sum())
                other.append(im2)
            else:
                logger.warning(
                    "Too few features in matching image {}: {}".format(
                        im2, len(words[im2])))
    return np.argsort(distances), other


def match_candidates_with_bow(data, images, max_neighbors):
    """Find candidate matching pairs using BoW-based distance."""
    if max_neighbors <= 0:
        return set()

    words = {im: data.load_words(im) for im in images}
    bows = bow.load_bows(data.config)

    pairs = set()
    for im in images:
        order, other = bow_distances(im, images, words, None, bows)
        for i in order[:max_neighbors]:
            pairs.add(tuple(sorted((im, other[i]))))
    return pairs


def match_candidates_by_distance(images, exifs, reference, max_neighbors, max_distance):
    """Find candidate matching pairs by GPS distance.

    The GPS altitude is ignored because we want images of the same position
    at different altitudes to be matched together.  Otherwise, for drone
    datasets, flights at different altitudes do not get matched.
    """
    if max_neighbors <= 0 and max_distance <= 0:
        return set()
    max_neighbors = max_neighbors or 99999999
    max_distance = max_distance or 99999999.
    k = min(len(images), max_neighbors + 1)

    points = np.zeros((len(images), 3))
    for i, image in enumerate(images):
        gps = exifs[image]['gps']
        points[i] = reference.to_topocentric(
            gps['latitude'], gps['longitude'], 0)

    tree = spatial.cKDTree(points)

    pairs = set()
    for i, image in enumerate(images):
        distances, neighbors = tree.query(
            points[i], k=k, distance_upper_bound=max_distance)
        for j in neighbors:
            if i != j and j < len(images):
                pairs.add(tuple(sorted((images[i], images[j]))))
    return pairs


def match_candidates_by_time(images, exifs, max_neighbors):
    """Find candidate matching pairs by time difference."""
    if max_neighbors <= 0:
        return set()
    k = min(len(images), max_neighbors + 1)

    times = np.zeros((len(images), 1))
    for i, image in enumerate(images):
        times[i] = exifs[image]['capture_time']

    tree = spatial.cKDTree(times)

    pairs = set()
    for i, image in enumerate(images):
        distances, neighbors = tree.query(times[i], k=k)
        for j in neighbors:
            if i != j and j < len(images):
                pairs.add(tuple(sorted((images[i], images[j]))))
    return pairs


def match_candidates_by_order(images, max_neighbors):
    """Find candidate matching pairs by sequence order."""
    if max_neighbors <= 0:
        return set()
    n = (max_neighbors + 1) // 2

    pairs = set()
    for i, image in enumerate(images):
        a = max(0, i - n)
        b = min(len(images), i + n)
        for j in range(a, b):
            if i != j:
                pairs.add(tuple(sorted((images[i], images[j]))))
    return pairs


def match_candidates_from_metadata(images, exifs, data):
    """Compute candidate matching pairs"""
    max_distance = data.config['matching_gps_distance']
    gps_neighbors = data.config['matching_gps_neighbors']
    time_neighbors = data.config['matching_time_neighbors']
    order_neighbors = data.config['matching_order_neighbors']
    bow_neighbors = data.config['matching_bow_neighbors']

    if not data.reference_lla_exists():
        data.invent_reference_lla()
    reference = data.load_reference()

    if not all(map(has_gps_info, exifs.values())):
        if gps_neighbors != 0:
            logger.warn("Not all images have GPS info. "
                        "Disabling matching_gps_neighbors.")
        gps_neighbors = 0
        max_distance = 0

    images.sort()

    if max_distance == gps_neighbors == time_neighbors == order_neighbors == bow_neighbors == 0:
        # All pair selection strategies deactivated so we match all pairs
        d = set()
        t = set()
        o = set()
        pairs = combinations(images, 2)
    else:
        d = match_candidates_by_distance(images, exifs, reference,
                                         gps_neighbors, max_distance)
        t = match_candidates_by_time(images, exifs, time_neighbors)
        o = match_candidates_by_order(images, order_neighbors)
        b = match_candidates_with_bow(data, images, bow_neighbors)
        pairs = d | t | o | b

    res = {im: [] for im in images}
    for im1, im2 in pairs:
        res[im1].append(im2)

    report = {
        "num_pairs_distance": len(d),
        "num_pairs_time": len(t),
        "num_pairs_order": len(o)
    }
    return res, report