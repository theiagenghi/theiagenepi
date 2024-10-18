import { Icon } from "czifui";
import { StyledDiv, StyledNewTabLink, StyledSpan } from "./styles";

const TreeCreateHelpLink = (): JSX.Element => {
  const HREF =
    "https://theiagenepi.zendesk.com/hc/en-us/articles/29454978786715-Build-on-demand-trees#customizing";
  return (
    <StyledDiv>
      <StyledNewTabLink href={HREF}>
        <Icon sdsIcon="lightBulb" sdsSize="s" sdsType="static" />
        <StyledSpan>How can I create my own tree?</StyledSpan>
        <Icon sdsIcon="chevronRight" sdsSize="xs" sdsType="static" />
      </StyledNewTabLink>
    </StyledDiv>
  );
};

export { TreeCreateHelpLink };
