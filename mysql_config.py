import mysql.connector

class useMYSQL:
    
    def __init__(self, server,user,password,db):
        self.server = server
        
        self.user = user
        self.password = password
        self.db = db
        
        try:
            self.db = mysql.connector.connect(
                host=server,
                user=user,
                password=password,
                database=db
            )
        except:
            return False
        
def getConfig(self):
    
    _cursor = self.db.cursor()
    
    _cursor.execute = ("SELECT * from repeaters where ENABLED=1")
    
    _res = cursor.fetchall()
    
    print(_res)
        
            #elif config.getboolean(section, 'ENABLED'):
                #if config.get(section, 'MODE') == 'PEER':
                    #CONFIG['SYSTEMS'].update({section: {
                        #'MODE': config.get(section, 'MODE'),
                        #'ENABLED': config.getboolean(section, 'ENABLED'),
                        #'LOOSE': config.getboolean(section, 'LOOSE'),
                        #'SOCK_ADDR': (gethostbyname(config.get(section, 'IP')), config.getint(section, 'PORT')),
                        #'IP': gethostbyname(config.get(section, 'IP')),
                        #'PORT': config.getint(section, 'PORT'),
                        #'MASTER_SOCKADDR': (gethostbyname(config.get(section, 'MASTER_IP')), config.getint(section, 'MASTER_PORT')),
                        #'MASTER_IP': gethostbyname(config.get(section, 'MASTER_IP')),
                        #'MASTER_PORT': config.getint(section, 'MASTER_PORT'),
                        #'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE'), 'utf-8'),
                        #'CALLSIGN': bytes(config.get(section, 'CALLSIGN').ljust(8)[:8], 'utf-8'),
                        #'RADIO_ID': config.getint(section, 'RADIO_ID').to_bytes(4, 'big'),
                        #'RX_FREQ': bytes(config.get(section, 'RX_FREQ').ljust(9)[:9], 'utf-8'),
                        #'TX_FREQ': bytes(config.get(section, 'TX_FREQ').ljust(9)[:9], 'utf-8'),
                        #'TX_POWER': bytes(config.get(section, 'TX_POWER').rjust(2,'0'), 'utf-8'),
                        #'COLORCODE': bytes(config.get(section, 'COLORCODE').rjust(2,'0'), 'utf-8'),
                        #'LATITUDE': bytes(config.get(section, 'LATITUDE').ljust(8)[:8], 'utf-8'),
                        #'LONGITUDE': bytes(config.get(section, 'LONGITUDE').ljust(9)[:9], 'utf-8'),
                        #'HEIGHT': bytes(config.get(section, 'HEIGHT').rjust(3,'0'), 'utf-8'),
                        #'LOCATION': bytes(config.get(section, 'LOCATION').ljust(20)[:20], 'utf-8'),
                        #'DESCRIPTION': bytes(config.get(section, 'DESCRIPTION').ljust(19)[:19], 'utf-8'),
                        #'SLOTS': bytes(config.get(section, 'SLOTS'), 'utf-8'),
                        #'URL': bytes(config.get(section, 'URL').ljust(124)[:124], 'utf-8'),
                        #'SOFTWARE_ID': bytes(config.get(section, 'SOFTWARE_ID').ljust(40)[:40], 'utf-8'),
                        #'PACKAGE_ID': bytes(config.get(section, 'PACKAGE_ID').ljust(40)[:40], 'utf-8'),
                        #'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME'),
                        #'OPTIONS': bytes(config.get(section, 'OPTIONS'), 'utf-8'),
                        #'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        #'SUB_ACL': config.get(section, 'SUB_ACL'),
                        #'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                        #'TG2_ACL': config.get(section, 'TGID_TS2_ACL')
                    #}})
                    #CONFIG['SYSTEMS'][section].update({'STATS': {
                        #'CONNECTION': 'NO',             # NO, RTPL_SENT, AUTHENTICATED, CONFIG-SENT, YES 
                        #'CONNECTED': None,
                        #'PINGS_SENT': 0,
                        #'PINGS_ACKD': 0,
                        #'NUM_OUTSTANDING': 0,
                        #'PING_OUTSTANDING': False,
                        #'LAST_PING_TX_TIME': 0,
                        #'LAST_PING_ACK_TIME': 0,
                    #}})
                    
if __name__ == '__main__':
    sql = useMYSQL("87.117.229.39","hblink","project999","hblink")
    
    sql.getConfig()
