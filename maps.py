import json
import logging
import os
import re
import urllib
from HTMLParser import HTMLParser
from math import atan2
from math import cos
from math import degrees
from math import radians
from math import sin
from urllib import urlencode

import flask
import redis
from flask import request
from geopy import geocoders

# XXX replace with twilio-scoped import once we publish the new lib
import twiml


DEBUG = False

app = flask.Flask(__name__)
streamhandler = logging.StreamHandler()
app.logger.addHandler(streamhandler)

geocoder = geocoders.GoogleV3()
redis_client = redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379'))

STATIC_MAPS_URI = 'https://maps.googleapis.com/maps/api/staticmap'
DEFAULT_MAPS_PARAMS = {'sensor': 'false', 'size': '640x640'}

DEFAULT_ZOOM = '15'

LAT_PAN_DISTANCE_MAP = {
    '20': 0.0005,
    '19': 0.001,
    '18': 0.0012,
    '17': 0.0015,
    '16': 0.004,
    '15': 0.0075,
    '14': 0.015,
    '13': 0.03,
    '12': 0.06,
    '11': 0.10,
    '10': 0.20,
    '9': 0.40,
    '8': 0.80,
    '7': 1.60,
    '6': 3.20,
}

LON_PAN_DISTANCE_MAP = {
    '20': 0.001,
    '19': 0.0012,
    '18': 0.0015,
    '17': 0.004,
    '16': 0.0075,
    '15': 0.015,
    '14': 0.03,
    '13': 0.06,
    '12': 0.10,
    '11': 0.20,
    '10': 0.40,
    '9': 0.80,
    '8': 1.60,
    '7': 3.20,
    '6': 6.40,
}

GMAPS_DIRECTIONS_URI = 'http://maps.googleapis.com/maps/api/directions/json?'
STREETVIEW_URI = 'http://maps.googleapis.com/maps/api/streetview'


class Directions(object):
    NORTH = 'north'
    SOUTH = 'south'
    EAST = 'east'
    WEST = 'west'
    IN = 'in'
    OUT = 'out'


KEYWORD_TO_DIRECTION = {
    'north': Directions.NORTH,
    'up': Directions.NORTH,
    'south': Directions.SOUTH,
    'down': Directions.SOUTH,
    'west': Directions.WEST,
    'left': Directions.WEST,
    'east': Directions.EAST,
    'right': Directions.EAST,
    'in': Directions.IN,
    'out': Directions.OUT,
}

DIRECTIONS_OR = '|'.join(KEYWORD_TO_DIRECTION.keys())
DIRECTIONS_RE = re.compile('^{}$'.format(DIRECTIONS_OR), re.IGNORECASE)

DESTINATION_RE = re.compile('^to:', re.IGNORECASE)


@app.route('/', methods=['POST'])
def handle_request():
    phone_number = request.form['From']
    body = request.form['Body']

    location = _get_stored_location(phone_number)
    nav_cmd = _parse_navigation(body)

    if nav_cmd is not None:
        if location:
            location = _apply_movement(location, nav_cmd)
        else:
            response = twiml.Response()
            response.Message(msg=u"Please enter a location to start from!")
            return unicode(response)
    elif DESTINATION_RE.match(body):
        # OK, get them some directions.
        destination = re.sub(DESTINATION_RE, '', body)
        # XXX use destination with current location place to get directions
        if (not location):
            response = twiml.Response()
            response.Message(msg=u"Please provide a starting location first.")
            return unicode(response)
        else:
            #we have both
            return unicode(get_steps(location["place"], destination))

    else:
        # Just show the location requested.
        place, (lat, lon) = geocoder.geocode(body)
        location = dict(place=place, lat=lat, lon=lon, zoom=DEFAULT_ZOOM)

    response = _build_map_response(location)
    _store_location(phone_number, location)

    return unicode(response)


def _build_map_response(location):
    map_params = {
        'center': '{},{}'.format(str(location['lat']), str(location['lon'])),
        'zoom': location['zoom'],
    }
    map_params.update(DEFAULT_MAPS_PARAMS)
    map_tile_url = '{}?{}'.format(
        STATIC_MAPS_URI,
        urlencode(map_params),
    )
    r = twiml.Response()
    msg = r.message()
    msg.media(map_tile_url)

    return r


def get_directions(orig, dest):
    #Takes in an origin & destination and returns the direction via google maps api
    origin = orig.split()
    destination = dest.split()

    new_origin = "origin="
    new_dest = "&destination="
    for c in origin:
        new_origin += c + "+"
        for d in destination:
            new_dest += d + "+"

    return GMAPS_DIRECTIONS_URI + new_origin + new_dest + "&sensor=false"


def _heading(start, end):
    """Compute compass heading between a pair of lat/lon points.

    Based on formulae found at
    http://www.movable-type.co.uk/scripts/latlong.html.
    """
    start_lat = radians(float(start['lat']))
    end_lat = radians(float(end['lat']))
    delta_lon = radians(float(end['lng']) - float(start['lng']))

    y = sin(delta_lon) * cos(end_lat)
    x = ((cos(start_lat) * sin(end_lat)) -
         (sin(start_lat) * cos(end_lat) * cos(delta_lon)))
    heading = degrees(atan2(y, x))
    normalized = (heading + 360) % 360
    return int(normalized)


def get_steps(orig, dest):
    # connect to google api json
    decodeme = get_directions(orig, dest)

    googleResponse = urllib.urlopen(decodeme)
    jsonResponse = json.loads(googleResponse.read())
    r = twiml.Response()
    for item in jsonResponse["routes"][0]["legs"][0]["steps"]:
        lat = item["start_location"]["lat"]
        lon = item["start_location"]["lng"]
        heading = _heading(item["start_location"], item["end_location"])
        instructions = strip_tags(item["html_instructions"])

        params = {
            'location': '{},{}'.format(str(lat), str(lon)),
            'heading': str(heading),
        }
        params.update(DEFAULT_MAPS_PARAMS)

        streetview_url = '{}?{}'.format(STREETVIEW_URI, urlencode(params))
        msg = r.message(msg=instructions)
        msg.media(streetview_url)

    return r


def _get_stored_location(phone_number):
    return redis_client.hgetall(phone_number)


def _store_location(phone_number, location_dict):
    redis_client.hmset(
        phone_number,
        location_dict,
    )


def _parse_navigation(body):
    if DIRECTIONS_RE.match(body):
        # Since a location string might contain a directional word,
        # require an *exact* match against one of our commands.
        return KEYWORD_TO_DIRECTION[body.lower()]

    return None


def _apply_movement(location, direction):
    lat, lon = float(location['lat']), float(location['lon'])
    zoom = int(location['zoom'])
    if direction is Directions.NORTH:
        pan_distance = LAT_PAN_DISTANCE_MAP[location['zoom']]
        lat += pan_distance

    elif direction is Directions.SOUTH:
        pan_distance = LAT_PAN_DISTANCE_MAP[location['zoom']]
        lat -= pan_distance

    elif direction is Directions.EAST:
        pan_distance = LON_PAN_DISTANCE_MAP[location['zoom']]
        lon += pan_distance

    elif direction is Directions.WEST:
        pan_distance = LON_PAN_DISTANCE_MAP[location['zoom']]
        lon -= pan_distance

    elif direction is Directions.IN:
        zoom += 1

    elif direction is Directions.OUT:
        zoom -= 1

    else:
        raise ValueError("Unknown direction {}".format(direction))

    return dict(lat=str(lat), lon=str(lon), zoom=str(zoom))


# HTMLParser subclass to strip all tags out of text.
# Taken from http://stackoverflow.com/a/925630
class MLStripper(HTMLParser):
    def __init__(self):
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return ''.join(self.fed)


def strip_tags(html):
    s = MLStripper()
    s.feed(html)
    return s.get_data()


if __name__ == '__main__':
    app.debug = True
    DEBUG = True
    app.run()
