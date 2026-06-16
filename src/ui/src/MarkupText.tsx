type MarkupTextProps = {
  text: string;
};

export default function MarkupText({ text }: MarkupTextProps) {
  const blocks = text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  if (blocks.length === 0) {
    return null;
  }

  return (
    <div className="markup-text">
      {blocks.map((block, index) =>
        isListBlock(block) ? (
          <ul key={index}>
            {block.split(/\n/).map((item) => (
              <li key={item}>{renderInline(item.replace(/^\s*[-*]\s+/, ''))}</li>
            ))}
          </ul>
        ) : (
          <p key={index}>{renderInline(block)}</p>
        ),
      )}
    </div>
  );
}

function isListBlock(block: string) {
  return block.split(/\n/).every((line) => /^\s*[-*]\s+/.test(line));
}

function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);

  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }

    return part;
  });
}
