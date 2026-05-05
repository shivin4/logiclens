import tree_sitter_java as tsjava
import tree_sitter
from tree_sitter import Language, Parser, Query

lang = Language(tsjava.language())
parser = Parser(lang)
code = b"""
public class MainMenu {
    public class RoundButton extends JButton {
        public RoundButton(String text) { super(text); }
    }
}
class GameView {
    public boolean isPlayerWins() { return true; }
}
"""
tree = parser.parse(code)

print('--- TESTING CLASS ---')
q_cls = Query(lang, '(class_declaration name: (identifier) @class.name) @class.def')
cursor = tree_sitter.QueryCursor(q_cls)
for idx, match in cursor.matches(tree.root_node):
    name_node = match.get('class.name')
    if name_node:
        name_node = name_node[0] if isinstance(name_node, list) else name_node
        print('FOUND CLASS:', name_node.text.decode('utf8'))

print('--- TESTING FUNC ---')
q_func = Query(lang, '(method_declaration name: (identifier) @function.name) @function.def')
cursor = tree_sitter.QueryCursor(q_func)
for idx, match in cursor.matches(tree.root_node):
    name_node = match.get('function.name')
    if name_node:
        name_node = name_node[0] if isinstance(name_node, list) else name_node
        print('FOUND FUNC:', name_node.text.decode('utf8'))
