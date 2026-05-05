from neo4j import GraphDatabase
URI = 'neo4j://127.0.0.1:7687'
AUTH = ('neo4j', 'password123')
with GraphDatabase.driver(URI, auth=AUTH) as driver:
    with driver.session() as session:
        result = session.run("MATCH (caller)-[:CALLS]->(target {name: 'isPlayerWins'}) RETURN caller.name")
        names = [r['caller.name'] for r in result]
        print('Callers for isPlayerWins:', names)
        
        result2 = session.run("MATCH (m)-[r]->(n {name: 'isPlayerWins'}) RETURN labels(m) AS l, type(r) AS t")
        for r in result2:
            print(r['l'], r['t'])
