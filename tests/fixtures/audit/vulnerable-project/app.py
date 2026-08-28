import os, sqlite3

API_KEY = 'sk-notarealkeyusedonlyinafixture0000'


def lookup(db, user_input):
    # nosec fixture: intentional SQL string concatenation
    return db.execute('select * from users where name = ' + user_input)


def run(cmd):
    return os.system(cmd)
