#!/usr/bin/env python3

import threading
import traceback
import requests
import pandas as pd
import pyodbc

from datetime import datetime
from flask import Flask, request
from pyrad.client import Client
from pyrad import dictionary, packet
from configparser import ConfigParser


# ==========================
# Load configuration
# ==========================
config = ConfigParser()
config.read('config.ini')

error_url = config['slack']['error_url']

sql_kws = config['database']
sql_kws = {x: sql_kws[x] for x in sql_kws}

radius_kws = dict(
    server=config['radius_server']['address'],
    secret=bytes(config['radius_server']['secret'], 'utf-8'),
    dict=dictionary.Dictionary("dictionary")
)

client = Client(**radius_kws)
client.timeout = 30

api = Flask(__name__)


# ==========================
# Database connection helper
# ==========================
def get_connection():
    """
    Returns a working pyodbc connection using the {SQL Server} driver.
    """
    conn_str = (
        'DRIVER={SQL Server};'
        f"SERVER={sql_kws['server']};"
        f"DATABASE={sql_kws['database']};"
        f"UID={sql_kws['username']};"
        f"PWD={sql_kws['password']};"
        'Encrypt=no;'
        'TrustServerCertificate=yes;'
        'Connection Timeout=5;'
    )
    return pyodbc.connect(conn_str)


# ==========================
# Error logging decorator
# ==========================
def error_logging(func):
    def wrapper(*args, **kws):
        try:
            func(*args, **kws)

        except TypeError:
            # Happens when there is no current RADIUS login
            # Not a problem, so we silently pass
            pass

        except Exception:
            data = dict(
                url=error_url,
                data=f"{datetime.now()} RADIUS API:\n{''.join(traceback.format_exc())}"
            )
            requests.post(**data)

    return wrapper


# ==========================
# Data query logic (now pandas)
# ==========================
def get_radius_data(username):
    with get_connection() as conn:
        # --- Session ID ---
        query_session = """
            SELECT rco_session_id
            FROM radius_calls_online
            WHERE rco_username = ?;
        """
        df_session = pd.read_sql(query_session, conn, params=[username])
        if df_session.empty:
            raise TypeError(f"No session found for user: {username}")
        session_id = df_session.iloc[0]['rco_session_id']

        # --- rta_data ---
        query_rta = """
            SELECT rta_data
            FROM radius_type_attribute AS a
            INNER JOIN radius_type AS b ON a.rta_type = b.rt_id
            INNER JOIN radius_data AS c ON b.rt_name = c.radius_type
            WHERE a.rta_sortorder = 6
            AND username = ?;
        """
        df_rta = pd.read_sql(query_rta, conn, params=[username])
        if df_rta.empty:
            raise ValueError(f"No rta_data found for user: {username}")
        rta_data = df_rta.iloc[0]['rta_data']

    return (username, session_id, rta_data)


# ==========================
# Flask routes
# ==========================
@api.route('/disconnect', methods=['POST'])
def disconnect():
    radius_username = request.args.get('d')
    if not radius_username:
        return {"error": "Missing parameter 'd'"}, 400

    @error_logging
    def process(radius_username):
        username, session_id, rta_data = get_radius_data(radius_username)
        attributes = {
            "Acct-Session-Id": session_id,
            "User-Name": username,
            "NAS-IP-Address": radius_kws['server']
        }
        req = client.CreateCoAPacket(code=packet.DisconnectRequest, **attributes)
        return client.SendPacket(req)

    threading.Thread(target=process, args=(radius_username,)).start()
    return '', 200


@api.route('/changespeed', methods=['POST'])
def change_speed():
    radius_username = request.args.get('d')
    if not radius_username:
        return {"error": "Missing parameter 'd'"}, 400

    @error_logging
    def process(radius_username):
        username, session_id, rta_data = get_radius_data(radius_username)
        attributes = {
            "Acct-Session-Id": session_id,
            "NetElastic-Qos-Profile-Name": rta_data,
            "User-Name": username,
            "NAS-IP-Address": radius_kws['server']
        }
        req = client.CreateCoAPacket(**attributes)
        return client.SendPacket(req)

    threading.Thread(target=process, args=(radius_username,)).start()
    return '', 200


# ==========================
# Main
# ==========================
if __name__ == "__main__":
    api.run(host='0.0.0.0', port=8000)
