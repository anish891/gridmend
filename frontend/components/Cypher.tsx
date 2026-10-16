const KEYWORDS = new Set([
  "MATCH", "OPTIONAL", "WHERE", "RETURN", "WITH", "UNWIND", "CALL", "YIELD", "ORDER", "BY",
  "LIMIT", "DISTINCT", "AS", "AND", "OR", "NOT", "IN", "DESC", "CREATE", "SET", "DELETE",
  "DETACH", "CASE", "WHEN", "THEN", "ELSE", "END", "IS", "NULL",
]);
const FUNCTIONS = new Set([
  "all", "none", "count", "sum", "collect", "sqrt", "coalesce", "type", "relationships", "nodes",
  "length", "endNode", "startNode", "size", "range", "last", "min", "max",
]);

// Light Cypher highlighting: keywords, functions, parameters, strings, labels and types.
export default function Cypher({ code }: { code: string }) {
  const tokens = code.split(/('[^']*'|\$\w+|algo\.\w+|:[A-Z_]+(?:\|[A-Z_]+)*|\b\w+\b)/g);
  return (
    <pre className="cypher">
      <code>
        {tokens.map((token, i) => {
          if (KEYWORDS.has(token)) return <span key={i} className="cy-kw">{token}</span>;
          if (token.startsWith("algo.")) return <span key={i} className="cy-algo">{token}</span>;
          if (FUNCTIONS.has(token)) return <span key={i} className="cy-fn">{token}</span>;
          if (token.startsWith("$")) return <span key={i} className="cy-param">{token}</span>;
          if (token.startsWith("'")) return <span key={i} className="cy-str">{token}</span>;
          if (/^:[A-Z]/.test(token)) return <span key={i} className="cy-label">{token}</span>;
          return token;
        })}
      </code>
    </pre>
  );
}
