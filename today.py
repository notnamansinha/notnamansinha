import datetime
from dateutil import relativedelta
import requests
import os
# pyrefly: ignore [missing-import]
from lxml import etree
import time
import hashlib

HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ.get('USER_NAME') or 'notnamansinha'
# Repositories to exclude from the LOC and commit counts (case-insensitive)
# e.g., IGNORED_REPOS = ['notnamansinha/DataSync', 'some-org/huge-repo']
IGNORED_REPOS = [
    'notnamansinha/ahmedabad-multimodal-transit'
]
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}



def format_plural(unit):
    """
    Returns a properly formatted number
    e.g.
    'day' + format_plural(diff.days) == 5
    >>> '5 days'
    'day' + format_plural(diff.days) == 1
    >>> '1 day'
    """
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        data = request.json()
        if 'errors' in data:
            raise Exception(func_name, ' has failed with GraphQL errors:', data['errors'], QUERY_COUNT)
        if data.get('data') is None:
            raise Exception(func_name, ' returned null data. Check token scopes (needs classic token with repo + read:user):', request.text, QUERY_COUNT)
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_commits(start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = simple_request(graph_commits.__name__, query, variables)
    return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] != None:
            return loc_counter_one_repo(owner, repo_name, data, cache_comment, request.json()['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total, my_commits)
        else: return 0
    force_close_file(data, cache_comment)
    if request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    """
    Recursively call recursive_loc (since GraphQL can only search 100 commits at a time) 
    only adds the LOC value of commits authored by me
    """
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    else: return recursive_loc(owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    """
    Uses GitHub's GraphQL v4 API to query all the repositories I have access to (with respect to owner_affiliation)
    Queries 60 repos at a time, because larger queries give a 502 timeout error and smaller queries send too many
    requests and also give a 502 error.
    Returns the total number of lines of code in all repositories
    """
    if edges is None:
        edges = []
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    if request.json()['data']['user']['repositories']['pageInfo']['hasNextPage']:
        edges += request.json()['data']['user']['repositories']['edges']
        return loc_query(owner_affiliation, comment_size, force_cache, request.json()['data']['user']['repositories']['pageInfo']['endCursor'], edges)
    else:
        all_edges = edges + request.json()['data']['user']['repositories']['edges']
        return cache_builder(all_edges, comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks each repository in edges to see if it has been updated since the last time it was cached.
    If it has, runs recursive_loc on that repository to update the LOC count.
    Robustly handles order changes, additions, and deletions in the repository list.
    """
    cached = True
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    
    # Read existing cache data if it exists
    try:
        with open(filename, 'r') as f:
            existing_lines = f.readlines()
    except FileNotFoundError:
        existing_lines = []
        if comment_size > 0:
            for _ in range(comment_size):
                existing_lines.append('This line is a comment block. Write whatever you want here.\n')
            with open(filename, 'w') as f:
                f.writelines(existing_lines)

    cache_comment = existing_lines[:comment_size]
    cache_data_lines = existing_lines[comment_size:]

    # Parse existing cache into a dictionary for fast lookup by repo hash
    cache_dict = {}
    for line in cache_data_lines:
        parts = line.split()
        if len(parts) >= 5:
            repo_hash, commit_count, my_commits, loc_add_val, loc_del_val = parts[:5]
            cache_dict[repo_hash] = {
                'commit_count': int(commit_count),
                'my_commits': int(my_commits),
                'loc_add': int(loc_add_val),
                'loc_del': int(loc_del_val)
            }

    # If force_cache is True or number of items is different, mark cached as False
    if force_cache or len(cache_data_lines) != len(edges):
        cached = False

    new_data_lines = []
    # For error recovery, we need to pass a valid list of lines to recursive_loc
    # We will construct a dummy data list that is updated as we go
    current_data_state = [''] * len(edges)

    for index, edge in enumerate(edges):
        repo_name = edge['node']['nameWithOwner']
        repo_hash = hashlib.sha256(repo_name.encode('utf-8')).hexdigest()

        try:
            current_commit_count = edge['node']['defaultBranchRef']['target']['history']['totalCount']
        except (TypeError, KeyError):
            current_commit_count = 0

        # Check if we can reuse the cached data
        if repo_hash in cache_dict and cache_dict[repo_hash]['commit_count'] == current_commit_count and not force_cache:
            entry = cache_dict[repo_hash]
            new_line = f"{repo_hash} {current_commit_count} {entry['my_commits']} {entry['loc_add']} {entry['loc_del']}\n"
        else:
            cached = False
            if current_commit_count > 0:
                owner, repo_name_only = repo_name.split('/')
                # Get the LOC count via recursive GraphQL queries
                loc = recursive_loc(owner, repo_name_only, current_data_state, cache_comment)
                # loc is (addition_total, deletion_total, my_commits)
                new_line = f"{repo_hash} {current_commit_count} {loc[2]} {loc[0]} {loc[1]}\n"
            else:
                new_line = f"{repo_hash} 0 0 0 0\n"

        new_data_lines.append(new_line)
        current_data_state[index] = new_line

    # Write the updated cache back to the file
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(new_data_lines)

    # Calculate totals and print the beautiful breakdown
    breakdown = []
    ignored_set = {repo.lower() for repo in IGNORED_REPOS}
    for index, line in enumerate(new_data_lines):
        loc = line.split()
        my_commits = int(loc[2])
        additions = int(loc[3])
        deletions = int(loc[4])
        repo_name = edges[index]['node']['nameWithOwner']
        
        # Exclude repos with inflated LOC stats or specifically ignored repos from the lines of code count
        if repo_name.lower() not in ignored_set and additions < 50000:
            loc_add += additions
            loc_del += deletions
            
        breakdown.append((repo_name, my_commits, additions, deletions))

    # Sort breakdown by additions descending to show the biggest contributors first
    breakdown.sort(key=lambda x: x[2], reverse=True)
    
    print('\n' + '=' * 80)
    print(f"{'Repository':<42} | {'Commits':<7} | {'Lines Added':<11} | {'Lines Deleted':<13}")
    print('=' * 80)
    for repo_name, my_commits, additions, deletions in breakdown:
        print(f"{repo_name:<42} | {my_commits:<7} | {additions:<11,} | {deletions:<13,}")
    print('=' * 80 + '\n')

    return [loc_add, loc_del, loc_add - loc_del, cached]



def force_close_file(data, cache_comment):
    """
    Forces the file to close, preserving whatever data was written to it
    """
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename, 'has had the partial data saved and closed.')

def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """
    Parse SVG files and update elements with my commits, stars, repositories, and lines written
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    
    # Justify stats
    commit_data = f"{commit_data:,}" if isinstance(commit_data, int) else str(commit_data)
    find_and_replace(root, 'commit_data', commit_data)
    find_and_replace(root, "commit_data_dots", "." * max(0, 24 - 1 - len(commit_data)) + " ")
    
    find_and_replace(root, 'repo_data', str(repo_data))
    find_and_replace(root, 'contrib_data', str(contrib_data))
    repo_text = f"{repo_data} {{Contributed: {contrib_data}}}"
    find_and_replace(root, "repo_data_dots", "." * max(0, 24 - 1 - len(repo_text)) + " ")
    
    justify_format(root, 'follower_data', follower_data, 5)
    justify_format(root, 'star_data', star_data, 5)
    
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """
    Updates and formats the text of the element, and modifes the amount of dots in the previous element to justify the new text on the svg
    """
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = '.' * (just_len - 1) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value
    """
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def commit_counter(comment_size):
    """
    Counts up my total commits, using the cache file created by cache_builder.
    """
    total_commits = 0
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    with open(filename, 'r') as f:
        data = f.readlines()
    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def user_getter(username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']

def follower_getter(username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    Returns the function result and the time differential
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Prints a formatted time differential
    Returns formatted result if whitespace is specified, otherwise returns raw result
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


if __name__ == '__main__':
    """
    Naman Sinha (notnamansinha), 2026
    Adapted from Andrew Grant (Andrew6rant), 2022-2025
    """
    print('Calculation times:')
    # define global variable for owner ID and calculate user's creation date
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 0)
    formatter('LOC (cached)', loc_time) if total_loc[-1] else formatter('LOC (no cache)', loc_time)
    commit_data, commit_time = perf_counter(commit_counter, 0)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    for index in range(len(total_loc)-1): total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite('assets/dark_mode.svg', commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('assets/light_mode.svg', commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])

    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
        '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % (user_time + loc_time + commit_time + star_time + repo_time + contrib_time)),
        ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
