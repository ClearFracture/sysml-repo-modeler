import { Copy, FileCode2 } from 'lucide-react';

type CodeViewProps = {
  interconnectionName: string;
  interconnectionSource: string;
};

export default function CodeView({ interconnectionName, interconnectionSource }: CodeViewProps) {
  const source = {
    name: interconnectionName,
    text: interconnectionSource,
  };
  const lines = source.text.split(/\r?\n/);

  return (
    <section className="code-view">
      <div className="code-controls">
        <div className="matrix-title">
          <FileCode2 size={18} />
          <div>
            <strong>{source.name}</strong>
            <span>Readable SysML source for show-and-tell review.</span>
          </div>
        </div>
        <div className="code-actions">
          <button className="tool-button" onClick={() => navigator.clipboard.writeText(source.text)} type="button">
            <Copy size={16} />
            <span>Copy</span>
          </button>
        </div>
      </div>
      <div className="code-frame">
        <pre className="code-listing" aria-label={`${source.name} source code`}>
          {lines.map((line, index) => (
            <span className="code-line" key={`${index}-${line}`}>
              <span className="code-line-number">{index + 1}</span>
              <code>{line || ' '}</code>
            </span>
          ))}
        </pre>
      </div>
    </section>
  );
}
