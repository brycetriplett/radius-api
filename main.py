#!/usr/bin/env python3

import threading
import traceback
import requests

from datetime import datetime
from pyodbc import connect
from flask import Flask, request
from pyrad.client import Client
from pyrad import dictionary, packet
from configparser import ConfigParser
from contextlib import contextmanager


config = ConfigParser()
config.read('config.ini')

error_url = config['slack']['error_url']

sql_kws = config['database']
sql_kws = {x:sql_kws[x] for x in sql_kws}

radius_kws = dict(
    server = config['radius_server']['address'],
    secret = bytes(config['radius_server']['secret'], 'utf-8'),
    dict = dictionary.Dictionary("dictionary")
)

client = Client(**radius_kws)
client.timeout = 30

api = Flask(__name__)


def error_logging(func):
    def wrapper(*args, **kws):
        try:
            func(*args, **kws)

        except TypeError:
            #happens when there is no current radius login
            #not a problem
            pass

        except Exception:
            data = dict(
                url=error_url,
                data=f"{datetime.now()} RADIUS API:\n{''.join(traceback.format_exc())}"
            )

            requests.post(**data)

    return wrapper


@api.route('/disconnect', methods=['POST'])
def disconnect():
    radius_username = request.args['d']

    #@error_logging
    def process(radius_username):
        username, session_id, rta_data = get_radius_data(radius_username)

        attributes = {
            "Acct-Session-Id" : session_id,
            "User-Name" : username,
            "NAS-IP-Address" : radius_kws['server']
        }
        
        request = client.CreateCoAPacket(code=packet.DisconnectRequest, **attributes)
        return client.SendPacket(request)

        
    t = threading.Thread(target=process, args=(radius_username,))
    t.start()

    return '', 200


@api.route('/changespeed', methods=['POST'])
def change_speed():
    radius_username = request.args['d']

    #@error_logging
    def process(radius_username):
        username, session_id, rta_data = get_radius_data(radius_username)
        attributes = {
            "Acct-Session-Id" : session_id,
            "NetElastic-Qos-Profile-Name" : rta_data,
            "User-Name" : username,
            "NAS-IP-Address" : radius_kws['server']
            }

        request = client.CreateCoAPacket(**attributes)
        return client.SendPacket(request)
    

    t = threading.Thread(target=process, args=(radius_username,))
    t.start()

    return '', 200


@contextmanager
def sql_connect(server, database, username, password):
    conn = connect((
        'DRIVER={ODBC Driver 17 for SQL Server};'
        f'SERVER={server};'
        f'DATABASE={database};'
        f'UID={username};'
        f'PWD={password};'
    ))
    
    try:
        yield conn.cursor()

    finally:
        conn.close()
        

def get_radius_data(username):
    with sql_connect(**sql_kws) as cursor:
        cursor.execute(
            f"""
            SELECT rco_session_id
            FROM radius_calls_online
            WHERE rco_username = '{username}';
            """
        )

        session_id = cursor.fetchone()[0]

        cursor.execute(
            f"""
            SELECT rta_data FROM radius_type_attribute AS a
            INNER JOIN radius_type AS b ON a.rta_type = b.rt_id
            INNER JOIN radius_data AS c ON b.rt_name = c.radius_type
            WHERE a.rta_sortorder = 6
            AND username = '{username}';
            """
        )
        
        rta_data = cursor.fetchone()[0]
    
    return (username, session_id, rta_data)    


if __name__ == "__main__":
    api.run(host='0.0.0.0', port=8000)