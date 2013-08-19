import flask
import redis
from flask import request
from geopy import geocoders
from urllib import urlencode
from twilio import twiml


app = flask.Flask(__name__)

geocoder = geocoders.GoogleV3()
redis_client = redis.from_url('redis://localhost:6379')

STATIC_MAPS_URI = 'https://maps.googleapis.com/maps/api/staticmap'
DEFAULT_MAPS_PARAMS = {'sensor': 'false', 'size': '640x640'}
DEFAULT_ZOOM = 15

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

@app.route('/', methods=['POST'])
def get_map():
    phone_number = request.form['From']
    body = request.form['Body']

    location = _get_stored_location(phone_number)

    if not location:
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


if __name__ == '__main__':
    app.debug = True
    app.run()
