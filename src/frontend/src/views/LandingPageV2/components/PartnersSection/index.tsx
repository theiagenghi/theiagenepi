import Image from "next/image";
import NcbiVirusLogoImg from "src/common/images/ncbi-logo.png";
import NextcladeLogoImg from "src/common/images/nextclade-logov2.png";
import NextstrainLogoImg from "src/common/images/nextstrain-logo-full.png";
import PangolinLogoImg from "src/common/images/pangolin-logo.png";
import TghiLogoImg from "src/common/images/tghi-logo-2color.png";
import UsherLogoImg from "src/common/images/usher-logo.png";
import { ROUTES } from "src/common/routes";
import {
  LogoItem,
  NcbiVirusLogoLink,
  NextcladeLogoLink,
  NextstrainLogoLink,
  PangolinLogoLink,
  TghiLogoLink,
  PartnerLinkRow,
  PartnersSectionContainer,
  UsherLogoLink,
} from "./style";

export default function IntroSection(): JSX.Element {
  return (
    <>
      <PartnersSectionContainer>
        <PartnerLinkRow aria-label="attribution logos">
          <LogoItem>
            <NcbiVirusLogoLink href={ROUTES.NCBI_VIRUS} target="_blank">
              <Image alt="NCBI Virus" src={NcbiVirusLogoImg} />
            </NcbiVirusLogoLink>
          </LogoItem>
          <LogoItem>
            <NextstrainLogoLink href={ROUTES.NEXTSTRAIN} target="_blank">
              <Image alt="Nextstrain" src={NextstrainLogoImg} />
            </NextstrainLogoLink>
          </LogoItem>
          <LogoItem>
            <TghiLogoLink href={ROUTES.BIOHUB} target="_blank">
              <Image
                alt="Theiagen Global Health Initiative"
                src={TghiLogoImg}
              />
            </TghiLogoLink>
          </LogoItem>
          <LogoItem>
            <UsherLogoLink href={ROUTES.USHER} target="_blank">
              <Image alt="Usher" src={UsherLogoImg} />
            </UsherLogoLink>
          </LogoItem>
          <LogoItem>
            <PangolinLogoLink href={ROUTES.PANGOLIN} target="_blank">
              <Image alt="Pangolin" src={PangolinLogoImg} />
            </PangolinLogoLink>
          </LogoItem>
          <LogoItem>
            <NextcladeLogoLink href={ROUTES.NEXTCLADE} target="_blank">
              <Image alt="Nextclade" src={NextcladeLogoImg} />
            </NextcladeLogoLink>
          </LogoItem>
        </PartnerLinkRow>
      </PartnersSectionContainer>
    </>
  );
}
