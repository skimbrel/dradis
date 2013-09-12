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
from rq import Queue
from flask import request
from geopy import geocoders

# XXX replace with twilio-scoped import once we publish the new lib
import twiml
from client import send_directions_page
from worker import conn


DEBUG = False

app = flask.Flask(__name__)
streamhandler = logging.StreamHandler()
app.logger.addHandler(streamhandler)

geocoder = geocoders.GoogleV3()
redis_client = redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379'))

worker_queue = Queue(connection=conn)

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

GMAPS_DIRECTIONS_URI = 'http://maps.googleapis.com/maps/api/directions/json'
STREETVIEW_URI = 'http://maps.googleapis.com/maps/api/streetview'


class TConDirections(object):
    FOOD = 'food'
    HOTEL = 'hotel'
    TCON = 'twiliocon'
    #todo the TCon directions


KEYWORD_TO_TCON = {
    'food': TConDirections.FOOD,
    'eats': TConDirections.FOOD,
    'hotel': TConDirections.HOTEL,
    'sleep': TConDirections.HOTEL,
    'rest': TConDirections.HOTEL,
    'twilio': TConDirections.TCON,
    'twiliocon': TConDirections.TCON,
    'tcon': TConDirections.TCON,
}

TCONDIRS_OR = '|'.join(KEYWORD_TO_TCON.keys())
TCONDIRS_RE = re.compile('^{}$'.format(TCONDIRS_OR), re.IGNORECASE)


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

HELP_STRING = u"""Send a location ("645 Harrison Street, San Francisco, CA") to get a map image in reply.

Navigate the map with directions, e.g. "north" or "out".

To get street directions: "To: 635 8th Street, San Francisco, CA"
"""

HELP_RE = re.compile('^help|usage', re.IGNORECASE)

PAGE_SIZE = 3
STEPS_KEY_TMPL = "steps:{phone_number}"


@app.route('/', methods=['POST'])
def handle_request():
    phone_number = request.form['From']
    body = request.form['Body']

    location = _get_stored_location(phone_number)

    # Handle all of our special case logic for TwilioCon
    # If you're grabbing the source, you can either change those
    # or remove them entirely
    preset = _parse_twiliocon_presets(body)
    nav_cmd = _parse_navigation(body)

    if preset is not None:
        response = _get_tcon_response(preset)
        return unicode(response)

    elif nav_cmd is not None:
        if location:
            location = _apply_movement(location, nav_cmd)
        else:
            return _error(u"Please enter a location to start from!")
    elif DESTINATION_RE.match(body):
        # OK, get them some directions.
        destination = re.sub(DESTINATION_RE, '', body)
        # XXX use destination with current location place to get directions
        if (not location):
            return _error(u"Please provide a starting location first.")
        else:
            # XXX store steps and enqueue first page
            steps = get_steps(location["place"], destination)
            _store_steps(phone_number, steps)
            _send_next_page(phone_number, PAGE_SIZE)
            return unicode(twiml.Response())
    elif HELP_RE.match(body):
        return _usage()

    else:
        # Just show the location requested.
        try:
            place, (lat, lon) = geocoder.geocode(body)
        except ValueError:
            return _error(u"Sorry, we couldn't find a unique match for that location.")
        location = dict(place=place, lat=lat, lon=lon, zoom=DEFAULT_ZOOM)


    response = _build_map_response(location)
    _store_location(phone_number, location)

    return unicode(response)



def _error(message):
    response = twiml.Response()
    response.message(msg=message)
    return unicode(response)


def _usage():
    response = twiml.Response()
    response.message(msg=HELP_STRING)
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
    params = {'origin': orig, 'destination': dest, 'sensor': 'false'}
    encoded = urlencode(params)
    return '{}?{}'.format(GMAPS_DIRECTIONS_URI, encoded)


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
    app.logger.info("requesting directions at {}".format(decodeme))

    googleResponse = urllib.urlopen(decodeme)
    jsonResponse = json.loads(googleResponse.read())
    steps = []
    for idx, item in enumerate(jsonResponse["routes"][0]["legs"][0]["steps"]):
        lat = item["start_location"]["lat"]
        lon = item["start_location"]["lng"]
        heading = _heading(item["start_location"], item["end_location"])
        instructions = "{}. {}".format(
            idx + 1,
            strip_tags(item["html_instructions"]),
        )

        params = {
            'location': '{},{}'.format(str(lat), str(lon)),
            'heading': str(heading),
        }
        params.update(DEFAULT_MAPS_PARAMS)

        streetview_url = '{}?{}'.format(STREETVIEW_URI, urlencode(params))
        steps.append({'text': instructions, 'image': streetview_url})

    end = jsonResponse["routes"][0]["legs"][0]["steps"][-1]["end_location"]
    params = {
        'location': '{},{}'.format(str(end["lat"]), str(end["lng"])),
        'heading': str(heading),
    }
    params.update(DEFAULT_MAPS_PARAMS)

    streetview_url = '{}?{}'.format(STREETVIEW_URI, urlencode(params))
    arrival_msg = "Hopefully you ended up somewhere looking sort of like this."
    steps.append({'text': arrival_msg, 'image': streetview_url})

    return steps


def _get_stored_location(phone_number):
    return redis_client.hgetall(phone_number)


def _store_location(phone_number, location_dict):
    redis_client.hmset(
        phone_number,
        location_dict,
    )


def _store_steps(phone_number, steps):
    encoded_steps = [json.dumps(step) for step in steps]
    key = STEPS_KEY_TMPL.format(phone_number=phone_number)

    # Nuke anything that was there before.
    redis_client.delete(key)

    redis_client.rpush(key, *encoded_steps)


def _send_next_page(phone_number, page_size):
    worker_queue.send(send_directions_page, phone_number, page_size)


def _parse_twiliocon_presets(body):
    if TCONDIRS_RE.match(body):
        print "body.lower: {}".format(body.lower())


        # Since a location string might contain a directional word,
        # require an *exact* match against one of our commands.
        return KEYWORD_TO_TCON[body.lower()]

    print "No keywords matched from body: {}".format(body)
    return None


def _parse_navigation(body):
    if DIRECTIONS_RE.match(body):
        # Since a location string might contain a directional word,
        # require an *exact* match against one of our commands.
        return KEYWORD_TO_DIRECTION[body.lower()]

    return None

def _get_tcon_response(command):

    r = twiml.Response()
    if command is TConDirections.HOTEL:
        hotel_msg = "Nearby hotel options (with address provided for easy copy paste)\n\n" \
                   "Intercontinental San Francisco - 888 Howard Street, SF CA\n"
        r.message(msg=hotel_msg)


    elif command is TConDirections.FOOD:
        food_msg = "Nearby food options (with address provided for easy copy paste)\n\n\n" \
                   "Source - Vegetarian/Vegan - 11 Division St, SF CA\n\n\n" \
                   "SO - Asian Fusion - 1010 Bryant St, SF CA\n\n\n" \
                   "Henry's Hunan - Chinese - 1016 Bryant St, SF CA\n\n\n" \
                   "Grand Pu Bah Thai - 88 Division St, SF CA\n\n\n" \
                   "Saffron 685 - 685 Townsend St, SF CA"
        r.message(msg=food_msg)
    elif command is TConDirections.TCON:
        tcon_msg = "TCon rooms TBD:\n\n"
        r.message(msg=tcon_msg)

    else:
        raise ValueError("Unknown Twiliocon command {}".format(command))
    return r





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
