import mysql.connector
from mysql.connector import errorcode
#import mysql.connector.pooling

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) Simon Adlem, G7RZU 2020,2021'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN; Norman Williams, M6NBP'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'


class useMYSQL:
    #Init new object
    def __init__(self, server,user,password,database,table,logger):
        self.server = server
        self.user = user
        self.password = password
        self.database = database
        self.table = table
        self.logger = logger

    #Connect
    def con(self):
            logger = self.logger
            try:
                self.db = mysql.connector.connect(
                    host=self.server,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                #    pool_name = "hblink_master",
                #    pool_size = 2
                )
            except mysql.connector.Error as err:
                if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                    logger.info('(MYSQL) username or password error')
                    return (False)
                elif err.errno == errorcode.ER_BAD_DB_ERROR:
                    logger.info('(MYSQL) DB Error')
                    return (False)
                else:
                    logger.info('(MYSQL) error: %s',err)
                    return(False)

            return(True) 
     
     #Close DB connection   
    def close(self):
        self.db.close()
            
    #Get config from DB
    def getConfig(self):
        
        CONFIG = {}
        CONFIG['SYSTEMS'] = {}
        
        _cursor = self.db.cursor()
        
        try:
            _cursor.execute("select * from {} where MODE='MASTER'".format(self.table))
        except mysql.connector.Error as err:
            _cursor.close()
            logger.info('(MYSQL) error, problem with cursor execute')
            raise Exception('Problem with cursor execute')
        
        for (callsign, mode, enabled, _repeat, max_peers, export_ambe, ip, port, passphrase, group_hangtime, use_acl, reg_acl, sub_acl, tgid_ts1_acl, tgid_ts2_acl, default_ua_timer, single_mode, voice_ident,ts1_static,ts2_static,default_reflector, announce_lang) in _cursor:
            try:
                CONFIG['SYSTEMS'].update({callsign: {
                            'MODE': mode,
                            'ENABLED': bool(enabled),
                            'REPEAT': bool(_repeat),
                            'MAX_PEERS': int(max_peers),
                            'IP': ip,
                            'PORT': int(port),
                            'PASSPHRASE': bytes(passphrase, 'utf-8'),
                            'GROUP_HANGTIME': int(group_hangtime),
                            'USE_ACL': bool(use_acl),
                            'REG_ACL': reg_acl,
                            'SUB_ACL': sub_acl,
                            'TG1_ACL': tgid_ts1_acl,
                            'TG2_ACL': tgid_ts2_acl,
                            'DEFAULT_UA_TIMER': int(default_ua_timer),
                            'SINGLE_MODE': bool(single_mode),
                            'VOICE_IDENT': bool(voice_ident),
                            'TS1_STATIC': ts1_static,
                            'TS2_STATIC': ts2_static,
                            'DEFAULT_REFLECTOR': int(default_reflector),
                            'GENERATOR': int(1),
                            'ANNOUNCEMENT_LANGUAGE': announce_lang
                        }})
                CONFIG['SYSTEMS'][callsign].update({'PEERS': {}})
            except TypeError:
                logger.info('(MYSQL) Problem with data from MySQL - TypeError, carrying on to next row')
                    
        return(CONFIG['SYSTEMS'])
            

#For testing 
if __name__ == '__main__':
    
    sql = useMYSQL("ip","user","pass","db")
    
    sql.con()
    
    print( sql.getConfig())
