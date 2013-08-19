import flask
import redis
from flask import request
from geopy import geocoders
from urllib import urlencode
from twilio import twiml
import json
import urllib2, urllib, pprint

app = flask.Flask(__name__)

geocoder = geocoders.GoogleV3()
redis_client = redis.from_url('redis://localhost:6379')

STATIC_MAPS_URI = 'https://maps.googleapis.com/maps/api/staticmap'
DEFAULT_MAPS_PARAMS = {'sensor': 'false', 'size': '640x640'}

DEFAULT_ZOOM = 15

GOOGLE_MAPS_URI = 'http://maps.googleapis.com/maps/api/directions/json?'
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

    return GOOGLE_MAPS_URI + new_origin + new_dest + "&sensor=false"

def get_steps():
    # connect to google api json
    decodeme = get_directions("182 Douglass Street San Francisco CA 94114", "Seattle, Washington")

    googleResponse = urllib.urlopen(decodeme)
    jsonResponse = json.loads(googleResponse.read())
    pprint.pprint(jsonResponse)

    steps = {}
    print "------------------------------------------------------------------------------------------------"

    #print jsonResponse["routes"][0]["legs"]
    pprint.pprint (jsonResponse["routes"][0]["legs"][0]["steps"][0])
    for item in jsonResponse["routes"][0]["legs"][0]["steps"]:
        print "start: {}".format(item["start_location"])
        print "end: {}".format(item["end_location"])

        steps.update({item["start_location"]["lat"]: item["start_location"]["lng"]})

        #print item["html_directions"]
        #pprint.pprint(item)
        print "+++++++++++++++++++++++"


    print "VALUES OF STEPS"
    print steps
    #for key, value in jsonResponse['routes']['legs']['steps']:
    #    print "key: {} value:{}".format(key, value)
    #test = json.dumps([s['steps'] for s in jsonResponse['routes']['legs']], indent=3)
    #print(test)

    #for key, value in jsonResponse:
    #    print "key: {} value: {}".format(key, value)

    #Encode our streetviews
    img = STREETVIEW_URI
    for key, value in DEFAULT_MAPS_PARAMS.items():
        img += key + "=" + value + "&"

    for key, value in steps.items():
        print img + "location=" + str(key) + "," + str(value)


def _get_stored_location(phone_number):
    return redis_client.hgetall(phone_number)


def _store_location(phone_number, location_dict):
    redis_client.hmset(
        phone_number,
        location_dict,
    )


if __name__ == '__main__':
    app.debug = True
    #app.run()
    get_steps()

