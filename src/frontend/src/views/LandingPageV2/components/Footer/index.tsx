import Image from "next/image";
import { useEffect } from "react";
import BiohubLogoImg from "src/common/images/TGHI_HorzLogo_White.png";
import CZILogoImg from "src/common/images/czi-logo.png";
import FooterLogo from "src/common/images/theiagenpi-white-logo.svg";
import { ROUTES } from "src/common/routes";
import {
  CZBiohubLogo,
  CZContainer,
  CZILogo,
  CZLogoContainer,
  FooterBottomContainer,
  FooterBottomLink,
  FooterBottomLinkDivider,
  FooterBottomLinks,
  FooterBottomSeparator,
  FooterContainer,
  FooterLogoContainer,
  FooterPartnerships,
  FooterTopContainer,
  FooterTopLink,
  FooterTopLinks,
  FooterTopListItem,
  Span,
} from "./style";

export default function Footer(): JSX.Element {
  return (
    <FooterContainer data-test-id="landing-footer">
      <FooterTopContainer>
        <FooterLogoContainer href={ROUTES.HOMEPAGE}>
          <FooterLogo title="TheiaGenEpi Home" />
        </FooterLogoContainer>
        <FooterTopLinks>
          <FooterTopListItem>
            <FooterTopLink href={ROUTES.GITHUB} target="_blank">
              Github
            </FooterTopLink>
          </FooterTopListItem>
          <FooterTopListItem>
            <FooterTopLink href={ROUTES.HELP_CENTER} target="_blank">
              Help Center
            </FooterTopLink>
          </FooterTopListItem>
          <FooterTopListItem>
            <FooterTopLink href={ROUTES.GALAGO} target="_blank">
              Galago
            </FooterTopLink>
          </FooterTopListItem>
        </FooterTopLinks>
      </FooterTopContainer>
      <FooterBottomContainer>
        <FooterBottomLinks>
          <FooterBottomLink href={ROUTES.PRIVACY} target="_blank">
            Privacy
          </FooterBottomLink>
          <FooterBottomLinkDivider>|</FooterBottomLinkDivider>
          <FooterBottomLink href={ROUTES.TERMS} target="_blank">
            Terms
          </FooterBottomLink>
          <FooterBottomLinkDivider>|</FooterBottomLinkDivider>
          <FooterBottomLink href={ROUTES.CONTACT_US_EMAIL}>
            Contact us
          </FooterBottomLink>
        </FooterBottomLinks>
        <FooterBottomSeparator />
        <FooterPartnerships>
          <CZContainer>
            <CZLogoContainer>
              <CZILogo href={ROUTES.CZI} target="_blank">
                <Span>Supported by</Span>
                <Image src={CZILogoImg} alt="Chan Zuckerberg Initiative" />
              </CZILogo>
              <CZBiohubLogo href={ROUTES.BIOHUB} target="_blank">
                <Span>In partnership with </Span>
                <Image
                  src={BiohubLogoImg}
                  alt="Theiagen Global Health Initiative"
                />
              </CZBiohubLogo>
            </CZLogoContainer>
          </CZContainer>
        </FooterPartnerships>
      </FooterBottomContainer>
    </FooterContainer>
  );
}
