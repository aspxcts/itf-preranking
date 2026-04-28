fetch(
  "https://ipin.itftennis.com/Umbraco/Surface/entrylist/entry-list?tennisEventId=417c717e-b51e-4c72-92ce-f58473a61a43&entryListId=c9742002-8040-42d9-a7f1-d37ce2f34297",
  {
    headers: {
      accept: "application/json, text/plain, */*",
      "accept-language": "en-US,en;q=0.9",
      "sec-ch-ua":
        '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
      "sec-ch-ua-mobile": "?0",
      "sec-ch-ua-platform": '"Windows"',
      "sec-fetch-dest": "empty",
      "sec-fetch-mode": "cors",
      "sec-fetch-site": "same-origin",
      cookie:
        "visid_incap_178373=R3pwXJcHTq68gxYGcjcpRU2vsGgAAAAAQUIPAAAAAAAPSQywmsyEAVWOYDaipS3y; _tt_enable_cookie=1; _ttp=01K3S2SQ5QSQGA4771C07ZH86P_.tt.1; visid_incap_2983347=r8jtHVvnQw2GOwgyCmgwKsh6tGgAAAAAQUIPAAAAAADcpnbUsbOUD8bGvVrmLyvL; visid_incap_1219476=7weqWUXdQ82190MtGcig96vITGkAAAAAQUIPAAAAAACjY/BAUl+/ZURVD0l5NHVA; csrf_token=f62046da-4379-4b7a-ac79-3f89650ceeb5; _ga=GA1.1.157193566.1756409682; _ga_DH6MMHJPYQ=GS2.2.s1776212183$o28$g0$t1776212183$j60$l0$h0; ttcsid_D0LHM03C77UFC0GSO0QG=1776212183726::U1wLqBBuZYi6X2AkF00Q.52.1776212193629.1; _ga_ZRMHKRE9CL=GS2.1.s1776212182$o66$g1$t1776212193$j49$l0$h0; ttcsid=1776212183727::6lsKqxk9CZ7JvRtQfS4A.46.1776212193629.0::1.7688.9677::7669.2.770.426::56093.51.0; ARRAffinity=bcbcd66544f5ddaf4cbead41410626b9ca9d605ee7d3a04c141d9e19b964fb41; ARRAffinitySameSite=bcbcd66544f5ddaf4cbead41410626b9ca9d605ee7d3a04c141d9e19b964fb41; nlbi_1219476=jk+tCk0EmV9DJ267qqw5JwAAAABU7aJ/tH1lgGH1kBU2MTDG; ASP.NET_SessionId=qzd5miu211oj0u4jgnun0z44; incap_ses_2101_178373=7nrsQhNfagdVCDEI8UAoHYZJ4GkAAAAA82S1sY05tPuRjeBBFGUCcA==; FCCDCF=%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B32%2C%22%5B%5C%2283e54901-ed40-4177-9a68-1b09f383a3ba%5C%22%2C%5B1771876841%2C120000000%5D%5D%22%5D%5D%5D; FCNEC=%5B%5B%22AKsRol_NGm34J7yrXmYaggvWrERU6hb_RfV4TQFav2FRDhdhFn4mU-KbESHvLWQzlD-zhPsoaVz-fr4l3XoJjqYfb12UEicC5GgZ21oXl_RIZk-BbPtNUKDpYD7xs2taC0Lzo0GDKRVg5ZAW6w-bb987cxqqh-HbhA%3D%3D%22%5D%5D; nlbi_178373=PYerIUFp/AX+g5IqtoSRdQAAAACVB5NNr2NwEhMLRIK2rjZ5; incap_ses_2101_1219476=vOLYe8neCmHIT0AI8UAoHSlQ4GkAAAAAUEcne9oH8TLoBF/ykAv+Xw==; .AspNet.Cookies=iViKVOfNQrdt-kPK1ZZbtKANmfxBCejMR0MvIdqp4ylEgJBqGONZfTthApw3njuJWpL8nxb7GBnUSrbdU_R3pgeoCWBSTgGNwHmrOo7A5_XH-yohk21wOTHJvi5qzB0spJVbZb0IrTgHdKfm-3Rex84_0oL7bgcPkp-hwhqkOeQUirgQwCGOk2_V7h7QPV_Kho2LrzebujqL7V0azbd39XkKocRKDZTQLL8G0Tq1nfh7kkhpNIwPsk4oSErUEKbiSPdTZa-WCsLqSMwsbEN8Ap5PMYUMVks9Pf1H_79AAxlwXq-RRRnY2Pp8XaJ8tHAfWCCPeE1AiDkdVnvog9QF4ranbSl64t81dfFwXJUbH1n9N4TOIC4XTtIijbCLJHaP-znMtqc5dc2oBHxgGybOqhP1cRgkh3mCqaIFZGMo4qDWsndVJdeJnwknvfAmA3Gc8scz1tS1857t_v906i0vYDG-9d175FwXhDx25SES30wxz3hwNamHXn042sdEtZmyoc43SkytX8T6IJGx5fPRXweeHVVutSQhImCaD5aTE6JCyfcnTAJIrG-3C_aEb2Ee5V0gb7sz0QZxwJtx5yXZyXgeLej-aF78GkP4wUndZpTdrPHyCrW8_fzFKJh90cqu2nfPMOqINgp55AbSTEXIMbk4aoln54t_JDZyNUkwycjNBhv1hrNQx8Sn3fTuEg75yC0uo135U2uJBF2WnNfQLBmGShgT-jm8CyalqAuj3qOtD2U0t9Hp5IYG452WCdml8mIc269RxQN7cwGLrDlgIEuFfWxyqBTWT3Kuc9EsDj6HkwcmDUYA4FS-0IrviBlncUB8oFiFZit9skDCY3Y4pbl-aDeDVPpVCbKXMNfNKDqwMmFe5nsmWVxqPpi1BYGIcxNbyRbJnc6db0W7vgsGTWxMuBvoY7bhex42BXt4aVba_-nCueURVKXMW4iQTQepgFx5JGOXUq36uMnzPPAITw; _ga_5MNBFHNM4V=GS2.1.s1776308586$o18$g0$t1776309168$j29$l0$h0",
      Referer:
        "https://ipin.itftennis.com/factsheet?tournamentId=417c717e-b51e-4c72-92ce-f58473a61a43&circuitId=4a17c0c7-3dd4-4193-b868-dadfdf16732f",
    },
    body: null,
    method: "GET",
  },
);
