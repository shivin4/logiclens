from neo4j import GraphDatabase
URI = 'neo4j://127.0.0.1:7687'
AUTH = ('neo4j', 'password123')
with GraphDatabase.driver(URI, auth=AUTH) as driver:
    with driver.session() as session:
        result = session.run("MATCH (n) WHERE n.name = 'isPlayerWins' RETURN n.file AS file, labels(n) AS labels")
        for r in result:
            print('File:', r['file'], 'Labels:', r['labels'])
