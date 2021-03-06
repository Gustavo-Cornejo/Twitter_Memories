import fileinput
import json
import os, sys
from _datetime import datetime
from .celery import celery_app
from google.cloud import storage
from configuration.app_config import GCPConfig, Config, CeleryConfig

sys.path.append(os.getcwd())

def is_retweet(tweet: dict):
    starts_with_RT = tweet['tweet']['full_text'][:2] == 'RT'
    user_mentions_not_empty = tweet['tweet']['entities']['user_mentions'] != []
    return starts_with_RT and user_mentions_not_empty


def is_valid_date(date: str):
    # "Sat Feb 08 21:48:16 +0000 2020"
    months = {
        'Jan': 0,
        'Feb': 1,
        'Mar': 2,
        'Apr': 3,
        'May': 4,
        'Jun': 5,
        'Jul': 6,
        'Aug': 7,
        'Sep': 8,
        'Oct': 9,
        'Nov': 10,
        'Dec': 11
    }
    return months[date.split()[1]] >= months[datetime.now().strftime('%h')]

def get_month_and_date(date: str):
    return date.split()[1], date.split()[2]

@celery_app.task
def process_tweets(user_id, app_config=Config):
    from twittermemories import create_app
    from twittermemories.models import User, UserSchema, Tweet, TweetSchema, db
    this_app = create_app(app_config)
    with this_app.app_context():
        # download tweet archive for user
        storage_client = storage.Client.from_service_account_json(this_app.config['CLOUD_STORAGE'].GCP_JSON)
        bucket = storage_client.bucket(this_app.config['CLOUD_STORAGE'].GCP_STORAGE_BUCKET)
        twitter_archive = bucket.blob(user_id + '.json')
        twitter_archive.download_to_filename(this_app.config['CELERY_CONFIG'].TEMPSTORAGE + user_id + '.json')
        
        # convert archive to traversable json format
        for line in fileinput.input(this_app.config['CELERY_CONFIG'].TEMPSTORAGE + user_id + '.json', inplace=True):
            if fileinput.lineno() == 1:
                print(line.replace('window.YTD.tweet.part0 =', ''), end = '')
            else:
                print(line, end='')
        fileinput.close()
        # traverse tweets, pull out relevant info and persist instances
        curr_user = User.query.filter_by(user_id=user_id).first()
        tweetList = json.load(open(this_app.config['CELERY_CONFIG'].TEMPSTORAGE + user_id + '.json', 'r'))
        
        for tweet in tweetList: 
            dateString = tweet['tweet']['created_at']
            if is_valid_date(dateString) and not is_retweet(tweet):
                # persist tweet
                month, date = get_month_and_date(dateString)
                new_tweet = Tweet(
                    tweet_id=tweet['tweet']['id'],
                    month=month,
                    day=date,
                    user= curr_user
                )
                db.session.add(new_tweet)
                db.session.commit()
        curr_user.file_status = 2
        db.session.commit()

        # delete the file from gcp storage and local storage
        twitter_archive.delete()
        os.remove(os.path.join(this_app.config['CELERY_CONFIG'].TEMPSTORAGE, user_id + '.json'))