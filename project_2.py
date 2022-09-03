
from googleapiclient.discovery import build
import requests
from bs4 import BeautifulSoup
from spacy_help_functions import extract_relations
import spacy
from spanbert import SpanBERT #Note:  pip install torchvision,  pip3 install boto3
import multiprocessing
import time
import signal
import unicodedata
import argparse

def initiate_query(API_key:str, search_engine_ID: str, query: str) -> dict:
    """
    Start a query using Google JSON API
    query: user query string
    return query result in its raw format (JSON in Python dict format)
    """

    service = build("customsearch", "v1",
                  developerKey=API_key) 

    # q: query and cx is our search engine ID 
    res = service.cse().list(
        q=query,
        cx=search_engine_ID,
    ).execute()

    return res


def extract_plain_text(link: str) -> str:
    """Use bs4 to extract plain text from the given url"""

    res = requests.get(link) #need to iterate top 10 links?
    content = res.content
    soup = BeautifulSoup(content, "html.parser")
    text = soup.find_all(text=True)

    plain_text = ''
    # filter out elements that we don't want
    blacklist = [
        '[document]',
        'noscript',
        'header',
        'html',
        'meta',
        'head', 
        'input', 
        'script',
        'style',
        'title']

    for t in text:
        if t.parent.name not in blacklist:
            plain_text += '{} '.format(t)

    # remove unwanted unicode chars
    plain_text = unicodedata.normalize("NFKD", plain_text)
    
    return plain_text


def truncate_plain_text(plain_text: str, cap: int=20000):
    """
    cap: truncate any char over cap
    """

    # print('length of plain text: ', len(plain_text))
    prev_len = len(plain_text)
    if len(plain_text) > cap:
        plain_text = plain_text[:cap]
        print('truncated from {} to {} characters'.format(prev_len, len(plain_text)))

    return plain_text


def update_query(X: list, q:str) -> str:

    print('============================\nUpdating query...\n============================\n')
    X.sort(key = lambda tup: tup.get("confidence"),reverse=True)

    # convert query string, q, into a list of words, and turn into lower case
    q_words = q.split()
    q_words = [wd.lower() for wd in q_words]

    qlen_before = len(q_words)
    for rel in X:
        if rel["object"] not in q_words:
            if rel["subject"] not in q_words:
                # print('Extending: obj: {}, sub: {}'.format(rel['object'], rel['subject']))
                q_words.extend([rel["object"], rel["subject"]])
                q = ' '.join(q_words)
                print('Query has been updated to: ', q)
                break
        else:
            continue

    # no valid relation tuple to augment the query
    if len(q_words) == qlen_before:
        return ''

    return q


def main():
    """main process"""

    # receive command line arguments
    parser = argparse.ArgumentParser("Usage of ISE Program")
    parser.add_argument("Google_API_Key", help="Google Custom Search Engine JSON API Key used in project 1", type=str)
    parser.add_argument("Google_Engine_ID", help="Google Custom Search Engine ID used in project 1", type=str)
    parser.add_argument("r", help="an integer between 1 and 4, indicating the relation to extract: 1 Schools_Attended, 2 Work_For, 3 Live_In, and 4 Top_Member_Employees", type=int)
    parser.add_argument("t", help="a real number between 0 and 1, indicating the \"extraction confidence threshold\"", type=float)
    parser.add_argument("q", help="seed query", type=str)
    parser.add_argument("k", help="an integer greater than 0, indicating the number of tuples that we request", type=int)
    args = parser.parse_args()    

    API_key = args.Google_API_Key
    search_engine_ID = args.Google_Engine_ID
    r = args.r
    t = args.t
    q = args.q
    k = args.k

    print('----------------------------------------------------------\n\n')
    print('Your input parameter:\n')
    print('API Key: ', API_key)
    print('Search engine ID: ', search_engine_ID)
    print('r: {}\nt: {}\nq: {}\nk: {}'.format(r, t, q, k))
    print('----------------------------------------------------------\n\n')

   
    #########################################################################
    # Schools_Attended: Subject: PERSON, Object: ORGANIZATION
    # Work_For: Subject: PERSON, Object: ORGANIZATION
    # Live_In: Subject: PERSON, Object: one of LOCATION, CITY, STATE_OR_PROVINCE, or COUNTRY
    # Top_Member_Employees: Subject: ORGANIZATION, Object: PERSON
    #########################################################################
    entities_of_interest_ls = [["ORGANIZATION", "PERSON"], 
                                ["ORGANIZATION", "PERSON"], 
                                ["ORGANIZATION", "PERSON", "LOCATION", "CITY", "STATE_OR_PROVINCE", "COUNTRY"], 
                                ["ORGANIZATION", "PERSON"]]
    # for Spacy; decide which entities to extract
    entities_of_interest = entities_of_interest_ls[r-1] # don't forget to -1 for correct list index

    # for filter SpanBERT predicted results, keeping only the targeted relations
    relation_ls = ('per:schools_attended', 'per:employee_of', 'per:cities_of_residence', 'org:top_members/employees')   # the four relations that we are interested in
    target_rel = relation_ls[r-1]

    num_of_iter = 0
    num_extracted = 0   # number of already extracted relations
    prev_url = []
    X = []  # the list to hold extracted relations


    while num_extracted < k:

        '''issue a query'''
        num_of_iter += 1
        print('Iteration #{}:', num_of_iter)
        print('\nIssuing a custom Google search engine query: {} \n\n'.format(q))
        q_res = initiate_query(API_key=API_key, search_engine_ID=search_engine_ID, query=q)

        '''use beautifulsoup to extract plain text'''
        for web_page in q_res['items']:

            link = web_page['link']
            print('\nProcessing webpage: {}\n'.format(link))

            # skip already seen URL
            if link in prev_url:
                continue
            else:
                prev_url.append(link)

            #BeautifulSoup Timeout if more than 20 seconds 
            signal.alarm(20)    
            try:
                plain_text = extract_plain_text(link)
            except Exception:
                continue
            else:
                # Reset the alarm
                signal.alarm(0)

            # truncate to 20000 characters
            plain_text = truncate_plain_text(plain_text, cap=20000)

            '''feed plain text into Spacy+BERT process'''
            # Load spacy model
            nlp = spacy.load("en_core_web_lg")  

            # Apply spacy model to raw text (to split to sentences, tokenize, extract entities etc.)
            doc = nlp(plain_text)  

            # Load pre-trained SpanBERT model
            spanbert = SpanBERT("./pretrained_spanbert")  

            # Extract relations
            relations = extract_relations(doc, spanbert, entities_of_interest, conf=t, r=r)
            # print("Relations: {}".format(dict(relations)))

            # keep only target relations
            # each key in "relations" is a tuple of (subject, relation type, object)
            # and value is the confidence score for that relation tuple
            for rel in relations.keys():
                try:
                    if rel[1] != target_rel:
                        continue
                    
                    # Check for duplicates in X
                    # only append to X if tuple not already exist OR confidence level is higher than already exist 
                    # use lower case for uniformalty 
                    duplicate = False
                    for item in X:
                        if item["subject"] == rel[0].lower():
                            if item["object"] == rel[2].lower():
                                if item["confidence"] > relations[rel]:
                                    duplicate = True
                                    break
                                elif item["confidence"] < relations[rel]:
                                    X.pop(item) 
                                    num_extracted -= 1
                                    duplicate = False
                                    break

                    if duplicate == False:
                        X.append({
                            'confidence': relations[rel],
                            'subject': rel[0].lower(),
                            'object': rel[2].lower()
                        })
                        num_extracted += 1
                        if num_extracted >= k:
                            break

                    else:
                        continue

                except Exception:
                    # sometimes there are weird keys such as integers so added this to avoid error
                    pass

            if num_extracted >= k:
                break

            print('Finish processing webpage: {}\n\nCurrent X: {}\n\nCurrent number of relations: {}\n\n'.format(web_page['link'], X, len(X)))

        # Update query q if k not reached yet 
        if num_extracted < k:
            q = update_query(X, q)
            if q == '':
                print('Unable to update query (no valid relation tuple). Quitting...\n')
                break
            

    '''print results'''
    print('--------------Result----------------')
    print('Confidence\t|Subject\t|Object\t\n')
    X.sort(key = lambda tup: tup.get("confidence"),reverse=True) #sort X in DESC confidence level
    for relation in X:
        print('Confidence: {}\tSubject: {}\tObject: {}'.format(relation["confidence"], relation["subject"], relation["object"]))

    print("\nNumber of relations: {}".format(num_extracted))
    print("\nTotal number of iterations: ", num_of_iter)



if __name__ == "__main__":
    
    main()