import { Tooltip } from "czifui";
import { NewTabLink } from "src/common/components/library/NewTabLink";

interface Props {
  children: React.ReactElement;
  value: string;
}

export const TreeTypeTooltip = ({ children, value }: Props): JSX.Element => {
  let content;

  switch (value) {
    case "Targeted":
      content = "Best for facilitating outbreak investigation.";
      break;
    case "Overview":
      content = `Best for generating a summary tree of samples of interest, in the context of genetically similar samples.`;
      break;
    case "Non-Contextualized":
      content =
        "Best for uncovering sampling bias in your own sampling effort.";
      break;
    default:
      content = "Unknown tree type.";
  }

  const TOOLTIP_TEXT = (
    <div>
      {content}{" "}
      <NewTabLink href="https://theiagengenomics5614.zendesk.com/hc/en-us/article_attachments/27402419300507">
        Learn more
      </NewTabLink>
    </div>
  );

  return (
    <Tooltip arrow placement="bottom-start" title={TOOLTIP_TEXT}>
      {children}
    </Tooltip>
  );
};
