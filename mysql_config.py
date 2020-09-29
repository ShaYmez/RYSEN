import mysql.connector
from mysql.connector import errorcode

class useMYSQL:
    #Init new object
    def __init__(self, server,user,password,database):
        self.server = server
        self.user = user
        self.password = password
        self.database = database

    #Connect
    def con(self):
            try:
                self.db = mysql.connector.connect(
                    host=self.server,
                    user=self.user,
                    password=self.password,
                    database=self.database
                )
            except mysql.connector.Error as err:
                if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                    return [False,'Username or password error']
                elif err.errno == errorcode.ER_BAD_DB_ERROR:
                    return [False,'DB Does Not Exist']
                else:
                    return [False,err]

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
            _cursor.execute("select * from repeaters where ENABLED=1 and MODE='MASTER'")
        except mysql.connector.Error as err:
            _cursor.close()
            return(False)
        
        for (callsign, mode, enabled, _repeat, max_peers, export_ambe, ip, port, passphrase, group_hangtime, use_acl, reg_acl, sub_acl, tgid_ts1_acl, tgid_ts2_acl, default_ua_timer, single_mode, voice_ident) in _cursor:
            
            CONFIG['SYSTEMS'].update({callsign: {
                        'MODE': mode,
                        'ENABLED': bool(enabled),
                        'REPEAT': bool(_repeat),
                        'MAX_PEERS': int(max_peers),
                        'IP': ip,
                        'PORT': int(port),
                        'PASSPHRASE': passphrase,
                        'GROUP_HANGTIME': int(group_hangtime),
                        'USE_ACL': bool(use_acl),
                        'REG_ACL': reg_acl,
                        'SUB_ACL': sub_acl,
                        'TG1_ACL': tgid_ts1_acl,
                        'TG2_ACL': tgid_ts2_acl,
                        'DEFAULT_UA_TIMER': int(default_ua_timer),
                        'SINGLE_MODE': bool(single_mode),
                        'VOICE_IDENT': bool(voice_ident)
                    }})
            CONFIG['SYSTEMS'][callsign].update({'PEERS': {}})
                    
            return(CONFIG)
            

#For testing 
if __name__ == '__main__':
    
    sql = useMYSQL("ip","user","pass","db")
    
    sql.con()
    
    print( sql.getConfig())
