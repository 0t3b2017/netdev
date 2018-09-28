#!/usr/bin/env python3

import re
import json
import sys
import requests
# import os
# from slackclient import SlackClient
from netmiko import ConnectHandler
from datetime import datetime


# Monta equipamento
def captura_device(device_type, node, username, password, factor):
    device = {
        'device_type': device_type,
        'ip': node,
        'username': username,
        'password': password,
        'global_delay_factor': factor,
    }
    return device


# Verifica tipo de equipamento baseado no arquivo hosts.txt
def get_device_type(node):
    with open('hosts.txt', 'r') as f:
        hosts = f.readlines()

    for host in hosts:
        if node in host:
            dev_type = host.split(':')[1]
            return dev_type
            break
    else:
        print('ERRO: Equipamento {} não encontrado no arquivo hosts.txt.'.format(node))
        sys.exit(1)


# Obtem lista de incidentes open, valida se é de BGP e monta lita com node/instance
def get_bgp_im():
    with open('alarmebgp.json', 'r') as f:
        alarmes = eval(f.read())

    im = alarmes['IncidentSearch']
    im_desc = list(filter(None, im['Description']))
    im_id = im['IncidentID']
    im_desc_str = ''.join(im_desc)

    if 'BGP PEER DOWN' in im_desc_str or '[VDOM - Exceeded the threshold of competitors sessions' in im_desc_str:
        instance = re.search(r'instance\s([\d\.]+)', im_desc_str).group(1)
        node = re.search(r'NODE:\s\[([\w-]+)', im_desc_str).group(1)

        ims_bgp_open = []
        ims_bgp_open.append((node, instance, im_id))
        return ims_bgp_open


# Conecta no node e valida sessão BGP
def cmd(device, instance, device_type):
    try:
        output = ''
        # Conecta no equipamento
        net_connect = ConnectHandler(**device)

        node = device['ip']

        if device_type == 'junos':
            show_bgp_neigh = net_connect.send_command('show bgp neighbor {}'.format(instance))
        elif device_type == 'cisco_nxos':
            show_bgp_neigh = net_connect.send_command('show ip bgp neighbor {}'.format(instance))
        elif device_type == 'cisco_ios':
            cmd = 'show ip bgp neighbor {}'.format(instance)
            #print(cmd)
            output += cmd
            output += '\n\n'
            show_bgp_neigh = net_connect.send_command(cmd)
            #print(show_bgp_neigh)
            #output += show_bgp_neigh

            try:
                bgp_state = re.search(r'BGP state = (\w+)', show_bgp_neigh).group(1)
            except:
                msg = 'Sessão BGP não encontrada!\n\n'
                #print(msg)
                output += msg
                sys.exit(1)

            if bgp_state == 'Established':
                bgp_uptime_ms = re.search(r'uptime: (\d+)', show_bgp_neigh).group(1)
                bgp_uptime = re.search(r'up for ([\d:]+)', show_bgp_neigh).group(1)

                msg = "{}: Sessão BGP {} is UP for {}.\n\n".format(node, instance, bgp_uptime)
                output += msg

                # print(cmd)

                cmd = 'ping {} repeat 1000'.format(instance)
                output += 'Pinging neighbor... [{}].\n\n'.format(cmd)
                check_ping = net_connect.send_command(cmd)
                #print(check_ping)
                output += check_ping
                output += '\n\n'

                success_rate = re.search(r'Success rate is (\d+)', check_ping).group(1)
                if int(success_rate) >= 90:
                    msg = 'Ping Success rate is OK: {}%\n\n'.format(success_rate)
                    #print(msg)
                    output += msg
                else:
                    msg = 'Ping Success rate is NOT OK: {}%\n\n'.format(success_rate)
                    print(msg)
                    output += msg

                if int(bgp_uptime_ms) > 1800000:
                    msg = "Sessão está UP a mais de 30min\n\n"
                    #print(msg)
                    output += msg
                    # Contabilizar queda desta sessão - salvar em arquivo com historico (node, instance, incidente id, data, total)
                    # Fechar IM com output
                else:
                    msg = "Sessão ainda não está UP a 30min...\n\n"
                    #print(msg)
                    output += msg
                    net_connect.disconnect()

            else:
                msg = "{}: BGP Session {} is NOT UP. Collecting Data...\n\n".format(node, instance)
                output += msg
                cmd = 'show ip route {}'.format(instance)
                #print(cmd)
                output += cmd
                output += '\n\n'
                show_ip_route_neigh = net_connect.send_command(cmd)
                output += show_ip_route_neigh
                output += '\n\n'
                ip_route_if = re.search(r'via (\w+[\d/]+)', show_ip_route_neigh).group(1)
                cmd = 'show running interface {}'.format(ip_route_if)
                output += cmd
                output += '\n\n'
                int_conf = net_connect.send_command(cmd)
                #print(int_conf)
                output += int_conf
                output += '\n\n'
                net_connect.disconnect()
        return output
    except:
        output = ''
        msg = 'ERROR: Could not connect to device.\n'
        print(msg)
        output += msg
        raise


"""
def send_slack_msg(output):
    # Declarar variável de ambinte do bash = 'env SLACK_API_TOKEN="TOKEN_HERE"'
    slack_token = os.environ["SLACK_API_TOKEN"]
    sc = SlackClient(slack_token)

    sc.api_call(
        "chat.postMessage",
        # channel="CCSP1N308", # uolcsredes_bgp
        channel="RCCSSXL10R",  # uolcsredes_ims
        text="NOVO TESTE! :)"
    )
"""


# Envia mensagem no slack
def send_slack_msg(output):
    url = 'https://hooks.slack.com/services/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'

    headers = {'Content-Type': 'application/json'}

    slack_data = {'text': output, 'icon_emoji': ':ghost:'}

    response = requests.post(
        url, data=json.dumps(slack_data),
        headers={'Content-Type': 'application/json'}
    )

    if response.status_code != 200:
        raise ValueError(
            'Request to slack returned an error %s, the response is:\n%s' % (response.status_code, response.text))


def main():
    username = 'roberto'
    password = 'P@ssw0rd'
    output = ''
    ims_bgp_open = get_bgp_im()

    if not ims_bgp_open:
        msg = 'No incidents found. Good luck!'
        print(msg)
        send_slack_msg(msg)
        sys.exit(0)

    for node, instance, incident_id in ims_bgp_open:
        msg = 'Checking Incident ID: {}\n\n'.format(incident_id)
        output += msg
        dev_type = get_device_type(node)

        if dev_type == 'js':
            device_type = 'junos'
        elif dev_type == 'ns':
            device_type = 'cisco_nxos'
        elif dev_type == 'is':
            device_type = 'cisco_ios'
        else:
            msg = 'Error - Device type not found.'
            output += msg
            print(msg)
            continue

        device = captura_device(device_type, node, username, password, 2)
        output += cmd(device, instance, device_type)
        # send_slack_msg(output)
        print("#" * 80)
        print(output)
        print("#" * 80)


if __name__ == "__main__":
    main()
