fetch(
  "https://ipin.itftennis.com/Umbraco/Surface/TennisEvent/dashboard?fromDate=2026-04-13&toDate=2027-10-15",
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
        "visid_incap_178373=R3pwXJcHTq68gxYGcjcpRU2vsGgAAAAAQUIPAAAAAAAPSQywmsyEAVWOYDaipS3y; _tt_enable_cookie=1; _ttp=01K3S2SQ5QSQGA4771C07ZH86P_.tt.1; visid_incap_2983347=r8jtHVvnQw2GOwgyCmgwKsh6tGgAAAAAQUIPAAAAAADcpnbUsbOUD8bGvVrmLyvL; visid_incap_1219476=7weqWUXdQ82190MtGcig96vITGkAAAAAQUIPAAAAAACjY/BAUl+/ZURVD0l5NHVA; csrf_token=f62046da-4379-4b7a-ac79-3f89650ceeb5; _ga=GA1.1.157193566.1756409682; _ga_DH6MMHJPYQ=GS2.2.s1776212183$o28$g0$t1776212183$j60$l0$h0; FCCDCF=%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B32%2C%22%5B%5C%2283e54901-ed40-4177-9a68-1b09f383a3ba%5C%22%2C%5B1771876841%2C120000000%5D%5D%22%5D%5D%5D; FCNEC=%5B%5B%22AKsRol90kPE_ov3VS2P8O4ieA4CGNapdtWtm7xuiJub6RSOI-cPszVwXd8YmapWgOldQVNGinkQJ0o2GYZALxolszeoG9wrNGe7FmbERoHFtJ4Gvx-ZJ0iLzCN_xupmU8lL_Kx47uuK6_nYMPDZExI-QY98ihV_n2g%3D%3D%22%5D%5D; ttcsid_D0LHM03C77UFC0GSO0QG=1776212183726::U1wLqBBuZYi6X2AkF00Q.52.1776212193629.1; _ga_ZRMHKRE9CL=GS2.1.s1776212182$o66$g1$t1776212193$j49$l0$h0; ttcsid=1776212183727::6lsKqxk9CZ7JvRtQfS4A.46.1776212193629.0::1.7688.9677::7669.2.770.426::56093.51.0; OpenIdConnect.nonce.tXi9MEWoiRRGFCRvWLEBAZXIsNaZx8OlB%2FPnrjxY6Z8%3D=TWdNTEpVUThud0UwOHRjNmJyWEF1SVpZWS1YQ2hqdk02LVZTaU5GVFZZZUsyWEgxYUxCYXVNenpyRmNQMnQxOWRiMTZ3eGpPMDM4TzRrS25RRDBMcG9fRHpncUdHTTk0b0dweno5Zk1VcWozYl9nQ1lna3BtS25SQXhDRFY4R3E2Rk1wQVpZT3YyemJUbVMtOElsbDZneWhMbWlLb2poS1VhMTRrOVV5MEVrbnVtdXNFWEp1MnRsQzlpRFJURm9yX0JPWE54aTF1Y1ZQYXd2eWN3Y1FtMXJwZjlob210Tm5zOTFvQ0M3UzdYbw%3D%3D; ARRAffinity=bcbcd66544f5ddaf4cbead41410626b9ca9d605ee7d3a04c141d9e19b964fb41; ARRAffinitySameSite=bcbcd66544f5ddaf4cbead41410626b9ca9d605ee7d3a04c141d9e19b964fb41; nlbi_1219476=jk+tCk0EmV9DJ267qqw5JwAAAABU7aJ/tH1lgGH1kBU2MTDG; incap_ses_2101_1219476=1H+zdV00YGWmOwgI8UAoHcs14GkAAAAAqMBzk6E9UhP3T0n0DZn/oA==; ASP.NET_SessionId=qzd5miu211oj0u4jgnun0z44; _ga_5MNBFHNM4V=GS2.1.s1776301527$o17$g0$t1776301527$j60$l0$h0; OpenIdConnect.nonce.dd5ENmo0BElCrPwkyrFbXi%2FrGI1hwV%2FT%2Ff4j9jV2iDY%3D=a1RveFViOTVpX1ozeUQwTXd2UUlWUmtUS2p4UTQ5bnB1di1QZDBNUFNDckstNndRMDJxTm5VSlExMU1Kd01HVy1QRzRPZjVOcUg4VzdCNzZSM0dNS0NMVEhIN3J1LXM2MTlvR0RVVDNKRU5PUVdDYnJVT0pWMHdYdDl0MXE4NzR6V00zUFEtNUE2ZldjSzNiSjhfNGxqOXBEQXZEcmloZ05rVHhrTVVrMWlNbUVLM1VSelpzYWd0MUxpd1Y3bkNEU1M1V1daekNGdWNuZEx3NzFrRUlVLU5BSHVzUkR5TzV5WUxLRWlwTEJ0WQ%3D%3D; .AspNet.Cookies=TzpnnwnVDNKaeieEugpuZm7nCdiQIgJCB-8rapiVnkrFkZGMWWnOu46kycMSEY30o3XgbpprOrnsIXO8R8KaqHxfZZgy0lfkMG80ZAoLaW_bg2tDtTnEl6BQVfewGdAgFP_rhqz5pqqFUsd_ciVjPU0NdCez7UxZ23rWK3Q9Cul302O4go0Et7n0Ga2SAT7zh5O7oRqfEDm_GnJrur_0Kr-BP6EJRCJiFeu_5f58Jj3jHBGgXyJOUAn0ObldUGciJsFzr9jjmjBV4myITVsYlY-Q4HRgR0NKaueL3Bm5vmljRHWLcIrAvhaZtbhLnvSNCCkh3e4vS_oNl39nphymve8ignMQeubagzBGJY5MnTmVjbrcYd1QVGWttjHy1jps50hrLPZOSj6Xr-ZmSrU8gqyhq9b22FDvy9OQYyR3qQaNtyLLrIahy-MiIrYAB4Z_vYBOj3DTNDT8IPVEp1mu3tEjG-fs4dK15a7jnML6IDUwWmg6iYLuYWUT6GOdc7pEq-rUeZMa1nz54qN68j-FcV-RTIxWZB8jMBiZMGwXw7l5cXle2ErUMSM80sqSBFd4nUmiRhS-q5dN2iMvtteGMTW7F8Xfo2wec02fGrm0fri466X60zc-5PEUDJJFcpzPvOYbxUeuVtlY5QOFjxxqnG89ihF9b71mU18sNd4unZpCGPEYhhpjWIYqGZpevg-Fdh2J6TqmFZi3fdgCHd0GD-MpZVy4U6C5fWPkKzCNwnjyQ31A_Y3VPr1ivlWwNbpFho7ML-LvqS9fNjASx1Rycknlt21eZFsnuNZNpU4I1P_Y9QjUd1aBRAry_VKQm7vyuTg7foyFis9EZwGmxyx-CBy7QZdd5s4rFDbwkVNi_0U",
      Referer: "https://ipin.itftennis.com/",
    },
    body: null,
    method: "GET",
  },
);
