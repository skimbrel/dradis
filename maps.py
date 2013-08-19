import logging
import os
import re
from urllib import urlencode

import flask
import redis
from flask import request
from geopy import geocoders

# XXX replace with twilio-scoped import once we publish the new lib
import twiml


app = flask.Flask(__name__)
streamhandler = logging.StreamHandler()
app.logger.addHandler(streamhandler)

geocoder = geocoders.GoogleV3()
redis_client = redis.from_url(os.getenv('REDIS_URL'))

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


GOOGLE_MAPS_URI = 'http://maps.googleapis.com/maps/api/directions/json?origin='
STREETVIEW_URI = 'http://maps.googleapis.com/maps/api/streetview?'


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


@app.route('/', methods=['POST'])
def get_map():
    phone_number = request.form['From']
    body = request.form['Body']

    location = _get_stored_location(phone_number)
    nav_cmd = _parse_navigation(body)

    if nav_cmd is not None:
        if location:
            location = _apply_movement(location, nav_cmd)
        else:
            response = twiml.Response()
            response.Message(body=u"Please enter a location to start from!")
            return unicode(response)
    else:
        # New location
        place, (lat, lon) = geocoder.geocode(body)
        location = dict(lat=lat, lon=lon, zoom=DEFAULT_ZOOM)

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


if __name__ == '__main__':
    app.debug = True
    app.run()
